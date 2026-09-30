from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

from .broker import PaperBroker
from .config import Settings
from .experiment import ExperimentSession, ExperimentSessionStore
from .finance_stack import FinanceIntelligenceStack
from .journal import TradeJournal
from .model_c import ModelCAdvisor
from .models import Decision, PositionGuard, Signal
from .news import AlpacaNewsService
from .risk import RiskManager
from .strategy import MomentumCatalystStrategy, OpeningRangeVwapStrategy


EASTERN = ZoneInfo("America/New_York")


class ModelRuntime:
    def __init__(self, name: str, role: str, label: str, strategy, risk: RiskManager) -> None:
        self.name = name
        self.role = role
        self.label = label
        self.strategy = strategy
        self.risk = risk
        self.logs: deque[dict] = deque(maxlen=300)
        self.latest_signals: dict[str, dict] = {}
        self.guards: dict[str, PositionGuard] = {}
        self.realized_pl = 0.0
        self.unrealized_pl = 0.0
        self.peak_equity = 0.0
        self.last_scan_log_at: datetime | None = None
        self.last_decision_at: datetime | None = None

    @property
    def capital(self) -> float:
        return self.risk.capital

    @property
    def equity(self) -> float:
        return self.capital + self.realized_pl + self.unrealized_pl

    @property
    def return_pct(self) -> float:
        return ((self.equity / self.capital) - 1) * 100 if self.capital else 0.0

    @property
    def drawdown_pct(self) -> float:
        peak = max(self.peak_equity, self.capital)
        return ((peak - self.equity) / peak) * 100 if peak else 0.0

    def refresh_peak(self) -> None:
        self.peak_equity = max(self.peak_equity, self.equity, self.capital)

    def log(self, level: str, message: str, **details) -> None:
        self.logs.appendleft(
            {
                "time": datetime.now(EASTERN).isoformat(timespec="seconds"),
                "level": level,
                "message": message,
                "details": details,
            }
        )


class TradingEngine:
    """Paper-only three-model experiment with frozen daily A/B/C virtual allocations."""

    def __init__(self, config: Settings, broker: PaperBroker) -> None:
        self.config = config
        self.broker = broker
        self.journal = TradeJournal(config.trade_log_dir)
        self.session_store = ExperimentSessionStore(config.trade_log_dir)
        self.session: ExperimentSession | None = self.session_store.load_today()
        self.news = AlpacaNewsService(config.api_key, config.secret_key)
        self.model_c_intelligence = FinanceIntelligenceStack.from_settings(config)
        self.model_c = ModelCAdvisor(
            config.model_c_llm_base_url,
            config.model_c_llm_model,
            config.model_c_llm_api_key,
            intelligence=self.model_c_intelligence,
        )

        self.models = {
            "A": ModelRuntime(
                "A",
                "CONTROL",
                "Opening Range / VWAP",
                OpeningRangeVwapStrategy(config.relative_volume_min),
                RiskManager(config, "A"),
            ),
            "B": ModelRuntime(
                "B",
                "CHALLENGER",
                "Momentum Catalyst",
                MomentumCatalystStrategy(config),
                RiskManager(config, "B"),
            ),
            "C": ModelRuntime(
                "C",
                "ADAPTIVE",
                "Fin-R1 + Kronos + FinBERT",
                self.model_c,
                RiskManager(config, "C"),
            ),
        }

        self.running = False
        self.entries_paused = False
        self.draining = False
        self.task: asyncio.Task | None = None
        self.last_account: dict = {}
        self.last_positions: list = []
        self.last_cycle_at: datetime | None = None
        self.last_cycle_error: str | None = None
        self.cycle_count = 0

        self.universe: list[str] = list(config.watchlist)
        self.universe_stats: dict[str, int] = {}
        self.universe_refreshed_at: datetime | None = None

        if self.session:
            self._configure_allocations(self.session)

    def _configure_allocations(self, session: ExperimentSession) -> None:
        for name, model in self.models.items():
            capital = float(session.allocations.get(name, 0.0))
            if capital <= 0:
                continue
            model.risk.configure_session_capital(capital)
            model.peak_equity = max(model.peak_equity, capital)

    @staticmethod
    def _is_three_way_session(session: ExperimentSession | None) -> bool:
        if session is None:
            return False
        allocations = session.allocations
        return (
            set(allocations) == {"A", "B", "C"}
            and all(float(allocations.get(name, 0.0)) > 0 for name in ("A", "B", "C"))
        )

    def upgrade_legacy_session_if_flat(
        self,
        *,
        account: dict,
        positions: list,
        open_orders: list,
        now: datetime | None = None,
    ) -> bool:
        """Migrate a same-day legacy A/B session only when the paper account is flat."""
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        existing = self.session_store.load_today(current)
        if existing is None or self._is_three_way_session(existing):
            self.session = existing
            return False
        if positions or open_orders:
            return False

        self.session = self.session_store.replace_legacy_with_three_way(
            float(account["equity"]),
            current,
        )
        self._configure_allocations(self.session)
        for model in self.models.values():
            model.log(
                "CONTROL",
                "Legacy A/B session migrated to equal A/B/C split",
                allocation=round(model.capital, 2),
                session_id=self.session.session_id,
            )
        return True

    async def start(self) -> None:
        if self.running:
            return

        self.config.validate()
        await self._sync_journal_orders()

        account = await asyncio.to_thread(self.broker.account_snapshot)
        if account.get("trading_blocked"):
            raise RuntimeError("Alpaca reports trading as blocked for this PAPER account.")

        now = datetime.now(EASTERN)
        existing_session = self.session_store.load_today(now)

        open_orders = await asyncio.to_thread(self.broker.open_orders)
        if open_orders:
            raise RuntimeError(
                f"Cannot start with {len(open_orders)} open Alpaca order(s). "
                "Wait for them to finish or use Emergency Flatten."
            )

        positions = await asyncio.to_thread(self.broker.positions)

        migrated = self.upgrade_legacy_session_if_flat(
            account=account,
            positions=positions,
            open_orders=open_orders,
            now=now,
        )
        if migrated:
            existing_session = self.session

        if positions and existing_session is None:
            symbols = ", ".join(sorted(str(position.symbol) for position in positions))
            raise RuntimeError(
                "Cannot create a clean A/B/C session while pre-existing PAPER positions exist "
                f"({symbols}). Use Emergency Flatten first."
            )

        self.session = existing_session or self.session_store.create_or_load(account["equity"], now)
        self._configure_allocations(self.session)

        if positions:
            self._recover_positions(positions)

        self.last_account = account
        self.last_positions = positions
        self.entries_paused = False
        self.draining = False
        self.running = True
        self.last_cycle_error = None
        self.session_store.set_state(self.session, "RUNNING", now)

        path = self.journal.ensure_today()
        for model in self.models.values():
            model.log(
                "INFO",
                "Session started",
                role=model.role,
                allocation=round(model.capital, 2),
                risk_per_trade=model.risk.risk_budget,
                max_position=model.risk.max_position_notional,
                daily_loss_limit=model.risk.daily_loss_limit,
                trade_log=str(path),
            )

        self.task = asyncio.create_task(self._loop())

    def _recover_positions(self, positions: list) -> None:
        open_trades = self.journal.open_trades()
        recovered_symbols: set[str] = set()

        for position in positions:
            symbol = str(position.symbol).upper()
            candidates = [
                row for row in open_trades
                if str(row.get("symbol", "")).upper() == symbol
                and row.get("model") in self.models
            ]
            if not candidates:
                raise RuntimeError(
                    f"Existing PAPER position {symbol} is not attributable to Model A/B/C. "
                    "Use Emergency Flatten before starting a clean experiment."
                )

            row = candidates[-1]
            model = self.models[row["model"]]
            context = row.get("signal_context") or {}
            entry = float(position.avg_entry_price)
            stop = float(context.get("stop_price") or (entry * (1 - self.config.stop_loss_pct)))
            qty = abs(float(position.qty))
            entered_raw = row.get("filled_at") or row.get("recorded_at")
            try:
                entered_at = datetime.fromisoformat(str(entered_raw))
                if entered_at.tzinfo is None:
                    entered_at = entered_at.replace(tzinfo=EASTERN)
            except Exception:
                entered_at = datetime.now(EASTERN)

            trade_id = row.get("trade_id") or f"recovered-{model.name}-{uuid4().hex[:10]}"
            model.guards[symbol] = PositionGuard(
                model=model.name,
                symbol=symbol,
                trade_id=trade_id,
                entry_price=entry,
                quantity=qty,
                high_watermark=max(entry, float(position.current_price)),
                stop_price=stop,
                entered_at=entered_at,
                current_price=float(position.current_price),
                unrealized_pl=float(position.unrealized_pl),
            )
            recovered_symbols.add(symbol)
            model.log("WARN", "Recovered open PAPER position after restart", symbol=symbol, qty=qty)

        if len(recovered_symbols) != len(positions):
            raise RuntimeError("Not all existing PAPER positions could be recovered safely.")

    async def pause_entries(self) -> None:
        if not self.running:
            raise RuntimeError("Engine is not running.")
        if self.draining:
            raise RuntimeError("Engine is already draining.")
        self.entries_paused = True
        if self.session:
            self.session_store.set_state(self.session, "PAUSED")
        for model in self.models.values():
            model.log("CONTROL", "New entries paused; open positions remain managed")

    async def resume_entries(self) -> None:
        if not self.running:
            raise RuntimeError("Engine is not running.")
        if self.draining:
            raise RuntimeError("Cannot resume while Drain & Stop is active.")
        self.entries_paused = False
        if self.session:
            self.session_store.set_state(self.session, "RUNNING")
        for model in self.models.values():
            model.log("CONTROL", "New entries resumed")

    async def request_drain(self) -> None:
        if not self.running:
            return
        self.entries_paused = True
        self.draining = True
        if self.session:
            self.session_store.set_state(self.session, "DRAINING")
        for model in self.models.values():
            model.log(
                "CONTROL",
                "Drain & Stop requested",
                detail="No new entries; existing positions continue to be managed until flat.",
            )

    async def shutdown(self) -> None:
        """Application lifecycle stop. Does not intentionally submit orders."""
        self.running = False
        task = self.task
        self.task = None
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        try:
            await self._sync_journal_orders()
        except Exception:
            pass
        for model in self.models.values():
            model.log("WARN", "Application process stopped")

    async def kill_switch(self) -> None:
        """Emergency action: stop scanning, cancel orders, and flatten PAPER positions."""
        self.entries_paused = True
        self.draining = False
        self.running = False

        task = self.task
        self.task = None
        if task and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            await asyncio.to_thread(self.broker.cancel_open_orders)
        except Exception as exc:
            for model in self.models.values():
                model.log("ERROR", "Could not cancel open orders", error=str(exc))

        positions = await asyncio.to_thread(self.broker.positions)
        for position in positions:
            symbol = str(position.symbol).upper()
            guard_owner = next(
                (
                    model for model in self.models.values()
                    if symbol in model.guards
                ),
                None,
            )
            try:
                if guard_owner:
                    guard = guard_owner.guards[symbol]
                    qty = min(abs(float(position.qty)), guard.quantity)
                    order = await asyncio.to_thread(
                        self.broker.sell_qty,
                        symbol,
                        qty,
                        f"model-{guard_owner.name.lower()}-flat-{uuid4().hex[:14]}",
                    )
                    self.journal.record_order(
                        order,
                        model=guard_owner.name,
                        side="SELL",
                        symbol=symbol,
                        reason="emergency_flatten",
                        trade_id=guard.trade_id,
                        session_id=self.session.session_id if self.session else None,
                        requested_qty=qty,
                        signal_price=float(position.current_price),
                        signal_context={
                            "entry": guard.entry_price,
                            "stop": guard.stop_price,
                            "high_watermark": guard.high_watermark,
                        },
                    )
                    guard.exit_pending = True
                    guard_owner.log("WARN", "Emergency flatten submitted", symbol=symbol, qty=qty)
                else:
                    await asyncio.to_thread(self.broker.close_position, symbol)
            except Exception as exc:
                for model in self.models.values():
                    model.log("ERROR", "Emergency flatten failed", symbol=symbol, error=str(exc))

        if self.session:
            self.session_store.set_state(self.session, "FLATTENING")
        await self._sync_journal_orders()

    async def _loop(self) -> None:
        while self.running:
            cycle_started = datetime.now(EASTERN)
            try:
                await self.run_cycle()
                self.last_cycle_error = None
            except Exception as exc:
                self.last_cycle_error = str(exc)
                for model in self.models.values():
                    model.log("ERROR", "Cycle failed", error=str(exc))
            self.last_cycle_at = cycle_started
            self.cycle_count += 1
            if self.running:
                await asyncio.sleep(self.config.poll_seconds)

    async def run_cycle(self) -> None:
        now = datetime.now(EASTERN)
        await self._sync_journal_orders()

        account = await asyncio.to_thread(self.broker.account_snapshot)
        positions = await asyncio.to_thread(self.broker.positions)
        self.last_account = account
        self.last_positions = positions
        position_map = {str(position.symbol).upper(): position for position in positions}

        for model in self.models.values():
            await self._manage_model_position(model, position_map, now)

        # A guard remains until Alpaca no longer reports the position.
        for model in self.models.values():
            for symbol, guard in list(model.guards.items()):
                position = position_map.get(symbol)
                if position is None and guard.exit_pending:
                    model.guards.pop(symbol, None)
                    model.log("TRADE", "Position confirmed closed", symbol=symbol)

        self._refresh_model_marks(position_map)

        if self.draining:
            experiment_positions = any(model.guards for model in self.models.values())
            if not experiment_positions and not positions:
                self.running = False
                self.draining = False
                if self.session:
                    self.session_store.set_state(self.session, "STOPPED", now)
                for model in self.models.values():
                    model.log("CONTROL", "Drain complete; engine stopped flat")
            return

        if self.entries_paused or not self._entry_session(now):
            return

        await self._refresh_universe_if_needed(now)
        symbols = list(dict.fromkeys([*self.universe, "SPY", "QQQ"]))
        bars = await asyncio.to_thread(self.broker.minute_bars, symbols)
        market_aligned = self._market_alignment(bars)
        await asyncio.to_thread(self.news.refresh, self.universe)

        locked = set(position_map)
        locked.update(
            symbol
            for model in self.models.values()
            for symbol in model.guards
        )

        total_exposure = self._actual_exposure(positions)
        session_equity = self.session.starting_equity if self.session else account["equity"]
        effective_global_exposure_pct = (
            max(self.config.max_account_exposure_pct, 0.80)
            if self.config.action_day_mode
            else self.config.max_account_exposure_pct
        )
        global_cap = session_equity * effective_global_exposure_pct
        global_room = max(0.0, global_cap - total_exposure)
        available_cash = max(0.0, float(account.get("cash", 0.0)))

        for model in self.models.values():
            if self.session and float(self.session.allocations.get(model.name, 0.0)) <= 0:
                continue
            if len(model.guards) >= model.risk.max_open_positions:
                model.log(
                    "RISK",
                    "Open-position cap reached",
                    open_positions=len(model.guards),
                    cap=model.risk.max_open_positions,
                )
                continue

            candidates = []
            rejection_counts: dict[str, int] = {}

            if model.name == "C":
                candidates = await self._scan_model_c(
                    model=model,
                    bars=bars,
                    market_aligned=market_aligned,
                    now=now,
                    locked=locked,
                )
            else:
                for symbol in self.universe:
                    if symbol in locked:
                        continue
                    catalyst = self.news.catalyst_for(symbol) if model.name == "B" else None
                    signal = model.strategy.evaluate(
                        symbol,
                        bars.get(symbol, self._empty_frame()),
                        market_aligned,
                        now,
                        catalyst,
                    )
                    payload = signal.to_dict()
                    model.latest_signals[symbol] = payload
                    for failed in payload.get("context", {}).get("failed_conditions", []):
                        rejection_counts[failed] = rejection_counts.get(failed, 0) + 1
                    if signal.decision == Decision.BUY:
                        candidates.append(signal)

                if (
                    model.last_scan_log_at is None
                    or now - model.last_scan_log_at >= timedelta(seconds=60)
                ):
                    model.log(
                        "SCAN",
                        "Scanner funnel",
                        scanned=len(self.universe),
                        qualified=len(candidates),
                        rejected_by=rejection_counts if model.name == "B" else None,
                        open_positions=len(model.guards),
                        open_cap=model.risk.max_open_positions,
                    )
                    model.last_scan_log_at = now

            if not candidates:
                continue

            candidates.sort(key=lambda item: item.score or item.relative_volume, reverse=True)
            entries_this_cycle = 0

            for candidate in candidates:
                if entries_this_cycle >= model.risk.max_entries_per_cycle:
                    break
                if len(model.guards) >= model.risk.max_open_positions:
                    break
                if candidate.symbol in locked:
                    continue

                model_exposure = sum(
                    guard.quantity * (guard.current_price or guard.entry_price)
                    for guard in model.guards.values()
                )
                model_room = max(0.0, model.risk.max_exposure - model_exposure)
                check = model.risk.entry_check(
                    candidate.price,
                    candidate.stop_price,
                    False,
                    now,
                    available_cash=available_cash,
                    global_room=global_room,
                    model_room=model_room,
                )
                if not check.allowed:
                    model.log("RISK", "Entry rejected", reason=check.reason, symbol=candidate.symbol)
                    continue

                trade_id = f"{model.name}-{uuid4().hex[:12]}"
                client_order_id = f"model-{model.name.lower()}-{trade_id.lower()}"
                order = await asyncio.to_thread(
                    self.broker.buy_qty,
                    candidate.symbol,
                    check.quantity,
                    client_order_id,
                )

                profile = model.risk.profile
                context = candidate.to_dict() | {
                    "risk_dollars": round(check.dollars_at_risk, 2),
                    "planned_notional": round(check.notional, 2),
                    "session_allocation": round(model.capital, 2),
                    "risk_per_trade_pct": float(profile["risk_per_trade_pct"]),
                    "model_max_exposure": model.risk.max_exposure,
                    "model_max_open_positions": model.risk.max_open_positions,
                }
                self.journal.record_order(
                    order,
                    model=model.name,
                    side="BUY",
                    symbol=candidate.symbol,
                    reason=candidate.reason,
                    trade_id=trade_id,
                    session_id=self.session.session_id if self.session else None,
                    requested_qty=check.quantity,
                    signal_price=candidate.price,
                    signal_context=context,
                )

                model.guards[candidate.symbol] = PositionGuard(
                    model=model.name,
                    symbol=candidate.symbol,
                    trade_id=trade_id,
                    entry_price=candidate.price,
                    quantity=check.quantity,
                    high_watermark=candidate.price,
                    stop_price=candidate.stop_price,
                    entered_at=now,
                    current_price=candidate.price,
                )
                locked.add(candidate.symbol)
                available_cash = max(0.0, available_cash - check.notional)
                global_room = max(0.0, global_room - check.notional)
                entries_this_cycle += 1
                model.log(
                    "TRADE",
                    "Paper buy submitted",
                    symbol=candidate.symbol,
                    qty=round(check.quantity, 4),
                    notional=round(check.notional, 2),
                    risk=round(check.dollars_at_risk, 2),
                    setup=candidate.setup,
                    score=round(float(candidate.score or 0.0), 2),
                    open_positions=len(model.guards),
                )

    async def _scan_model_c(
        self,
        *,
        model: ModelRuntime,
        bars: dict,
        market_aligned: bool,
        now: datetime,
        locked: set[str],
    ) -> list[Signal]:
        if not self.config.model_c_enabled:
            if model.last_scan_log_at is None or now - model.last_scan_log_at >= timedelta(seconds=60):
                model.log("SCAN", "Model C disabled by configuration")
                model.last_scan_log_at = now
            return []

        if not self.model_c.configured:
            if model.last_scan_log_at is None or now - model.last_scan_log_at >= timedelta(seconds=60):
                model.log(
                    "WARN",
                    "Model C waiting for Fin-R1 endpoint",
                    required=["MODEL_C_LLM_BASE_URL", "MODEL_C_LLM_MODEL"],
                )
                model.last_scan_log_at = now
            return []

        stack_status = self.model_c_intelligence.status()
        if self.config.model_c_require_full_stack:
            missing = [
                name
                for name, state in stack_status.items()
                if state.get("enabled") and not state.get("ready")
            ]
            if missing:
                if model.last_scan_log_at is None or now - model.last_scan_log_at >= timedelta(seconds=60):
                    model.log(
                        "WARN",
                        "Model C full stack not ready",
                        missing=missing,
                        stack=stack_status,
                    )
                    model.last_scan_log_at = now
                return []

        if (
            model.last_decision_at is not None
            and now - model.last_decision_at
            < timedelta(seconds=self.config.model_c_decision_interval_seconds)
        ):
            return []

        catalysts = {
            symbol: self.news.catalyst_for(symbol)
            for symbol in self.universe
            if symbol not in locked
        }
        shortlist = self.model_c.shortlist(
            self.universe,
            bars,
            now=now,
            catalysts=catalysts,
            market_aligned=market_aligned,
            locked=locked,
            limit=self.config.model_c_shortlist_size,
        )

        if shortlist:
            shortlist = await asyncio.to_thread(
                self.model_c.enrich_shortlist,
                shortlist,
                bars_by_symbol=bars,
                catalysts=catalysts,
            )

            if self.config.model_c_require_full_stack:
                enrichment_errors = {
                    item["symbol"]: {
                        key: item.get(key, {}).get("error")
                        for key in ("finbert", "kronos")
                        if item.get(key, {}).get("error")
                    }
                    for item in shortlist
                }
                enrichment_errors = {key: value for key, value in enrichment_errors.items() if value}
                if enrichment_errors:
                    model.log(
                        "ERROR",
                        "Model C intelligence enrichment failed",
                        errors=enrichment_errors,
                    )
                    model.last_decision_at = now
                    return []

        for item in shortlist:
            preview = Signal(
                symbol=item["symbol"],
                decision=Decision.HOLD,
                price=float(item["price"]),
                vwap=float(item["vwap"]),
                opening_high=float(item["hod"]),
                relative_volume=float(item["relative_volume"]),
                market_aligned=market_aligned,
                reason=f"Fin-R1 shortlist pre-score {float(item['pre_score']):.2f}",
                timestamp=now,
                setup="llm_shortlist",
                gap_pct=float(item["gap_pct"]),
                change_pct=float(item["change_pct"]),
                stop_price=float(item["price"]) * 0.985,
                catalyst=bool(item["catalyst"]),
                catalyst_headline=item.get("catalyst_headline"),
                score=float(item["pre_score"]),
                context={"candidate": item},
            )
            model.latest_signals[item["symbol"]] = preview.to_dict()

        if not shortlist:
            if model.last_scan_log_at is None or now - model.last_scan_log_at >= timedelta(seconds=60):
                model.log("SCAN", "Model C found no active shortlist", scanned=len(self.universe))
                model.last_scan_log_at = now
            model.last_decision_at = now
            return []

        benchmark_context = {}
        for benchmark in ("SPY", "QQQ"):
            frame = bars.get(benchmark)
            if frame is None or frame.empty:
                continue
            current = float(frame.iloc[-1]["close"])
            previous = float(frame.iloc[-2]["close"]) if len(frame) >= 2 else current
            benchmark_context[benchmark] = {
                "price": current,
                "last_bar_change_pct": ((current / previous) - 1) * 100 if previous else 0.0,
            }

        market_context = {
            "timestamp": now.isoformat(timespec="seconds"),
            "market_aligned": market_aligned,
            "benchmarks": benchmark_context,
            "paper_only": True,
            "long_only": True,
            "open_model_c_positions": list(model.guards),
            "remaining_model_exposure": round(
                max(
                    0.0,
                    model.risk.max_exposure
                    - sum(
                        guard.quantity * (guard.current_price or guard.entry_price)
                        for guard in model.guards.values()
                    ),
                ),
                2,
            ),
        }

        model.last_decision_at = now
        try:
            decision = await asyncio.to_thread(
                self.model_c.decide,
                shortlist,
                market_context=market_context,
                timeout_seconds=self.config.model_c_llm_timeout_seconds,
            )
        except Exception as exc:
            model.log("ERROR", "Model C Fin-R1 decision failed", error=str(exc))
            return []

        selected = self.model_c.signal_from_decision(
            shortlist,
            decision,
            now=now,
            market_aligned=market_aligned,
            min_confidence=self.config.model_c_min_confidence,
        )

        decision_name = str(decision.get("decision", "HOLD")).upper()
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        model.log(
            "SCAN",
            "Model C Fin-R1 desk decision",
            decision=decision_name,
            symbol=decision.get("symbol"),
            confidence=round(confidence, 4),
            shortlist=[item["symbol"] for item in shortlist],
            rationale=decision.get("rationale"),
        )
        model.last_scan_log_at = now

        if selected is None:
            return []

        model.latest_signals[selected.symbol] = selected.to_dict()
        if not self.config.model_c_execution_enabled:
            model.log(
                "CONTROL",
                "Model C BUY kept in shadow mode",
                symbol=selected.symbol,
                confidence=round(confidence, 4),
            )
            return []

        return [selected]

    async def _manage_model_position(
        self,
        model: ModelRuntime,
        position_map: dict,
        now: datetime,
    ) -> None:
        for symbol, guard in list(model.guards.items()):
            position = position_map.get(symbol)
            if position is None:
                continue

            current = float(position.current_price)
            entry = float(position.avg_entry_price)
            guard.entry_price = entry
            guard.current_price = current
            guard.unrealized_pl = float(position.unrealized_pl)
            guard.high_watermark = max(guard.high_watermark, current)

            # Rebase the stop once the broker provides the actual fill so slippage at entry
            # cannot silently increase the intended dollar risk.
            if not guard.fill_risk_rebased and guard.quantity > 0:
                fill_budget_stop = entry - (model.risk.risk_budget / guard.quantity)
                old_stop = guard.stop_price
                guard.stop_price = max(guard.stop_price, fill_budget_stop)
                guard.fill_risk_rebased = True
                model.log(
                    "RISK",
                    "Stop rebased from actual fill",
                    symbol=symbol,
                    fill=round(entry, 4),
                    old_stop=round(old_stop, 4),
                    new_stop=round(guard.stop_price, 4),
                    target_risk=round(model.risk.risk_budget, 2),
                )

            if guard.exit_pending:
                continue

            reason = model.risk.exit_reason(
                entry,
                current,
                guard.high_watermark,
                guard.stop_price,
                entered_at=guard.entered_at,
                now=now,
            )
            if now.time() >= time(15, 50):
                reason = "end_of_day"

            if not reason:
                continue

            qty = min(abs(float(position.qty)), guard.quantity)
            order = await asyncio.to_thread(
                self.broker.sell_qty,
                symbol,
                qty,
                f"model-{model.name.lower()}-exit-{uuid4().hex[:14]}",
            )
            self.journal.record_order(
                order,
                model=model.name,
                side="SELL",
                symbol=symbol,
                reason=reason,
                trade_id=guard.trade_id,
                session_id=self.session.session_id if self.session else None,
                requested_qty=qty,
                signal_price=current,
                signal_context={
                    "entry": entry,
                    "stop": guard.stop_price,
                    "high_watermark": guard.high_watermark,
                },
            )
            guard.exit_pending = True
            model.log(
                "TRADE",
                "Paper exit submitted",
                symbol=symbol,
                reason=reason,
                qty=round(qty, 4),
            )

    def _refresh_model_marks(self, position_map: dict) -> None:
        for model in self.models.values():
            unrealized = 0.0
            for symbol, guard in model.guards.items():
                position = position_map.get(symbol)
                if position is not None:
                    guard.current_price = float(position.current_price)
                    guard.unrealized_pl = float(position.unrealized_pl)
                    unrealized += guard.unrealized_pl
            model.unrealized_pl = unrealized
            model.refresh_peak()

    async def _sync_journal_orders(self) -> None:
        for order_id in self.journal.pending_order_ids():
            try:
                order = await asyncio.to_thread(self.broker.order, order_id)
                self.journal.update_order(order)
            except Exception as exc:
                for model in self.models.values():
                    model.log("WARN", "Order-status sync failed", order_id=order_id, error=str(exc))

        summary = self.journal.status().get("summary", {}).get("models", {})
        for name, model in self.models.items():
            stats = summary.get(name, {})
            model.realized_pl = float(stats.get("realized_pl", 0.0) or 0.0)
            model.risk.load_performance(
                realized_pl=model.realized_pl,
                closed_trades=int(stats.get("closed_trades", 0) or 0),
                wins=int(stats.get("wins", 0) or 0),
                losses=int(stats.get("losses", 0) or 0),
                consecutive_losses=int(stats.get("consecutive_losses", 0) or 0),
            )

    async def _refresh_universe_if_needed(self, now: datetime) -> None:
        if not self.config.dynamic_universe:
            self.universe = list(self.config.watchlist)
            return

        refresh_after = timedelta(minutes=self.config.universe_refresh_minutes)
        if self.universe_refreshed_at and now - self.universe_refreshed_at < refresh_after:
            return

        selected, stats = await asyncio.to_thread(self.broker.discover_universe)
        if selected:
            self.universe = selected
            self.universe_stats = stats
            self.universe_refreshed_at = now
            for model in self.models.values():
                model.log("INFO", "Universe refreshed", **stats)
        else:
            for model in self.models.values():
                model.log("WARN", "Universe refresh returned no candidates; keeping prior universe")

    @staticmethod
    def _actual_exposure(positions: list) -> float:
        exposure = 0.0
        for position in positions:
            market_value = getattr(position, "market_value", None)
            if market_value not in (None, ""):
                exposure += abs(float(market_value))
            else:
                exposure += abs(float(position.qty) * float(position.current_price))
        return exposure

    @staticmethod
    def _entry_session(now: datetime) -> bool:
        return now.weekday() < 5 and time(9, 35) <= now.time() <= time(14, 30)

    @staticmethod
    def _market_alignment(bars: dict) -> bool:
        for benchmark in ("SPY", "QQQ"):
            frame = bars.get(benchmark)
            if (
                frame is not None
                and len(frame) >= 2
                and float(frame.iloc[-1]["close"]) > float(frame.iloc[-2]["close"])
            ):
                return True
        return False

    @staticmethod
    def _empty_frame():
        import pandas as pd
        return pd.DataFrame()

    def status(self) -> dict:
        journal = self.journal.status()
        summary = journal.get("summary", {}).get("models", {})
        now = datetime.now(EASTERN)

        account_equity = float(self.last_account.get("equity", 0.0) or 0.0)
        if self.session:
            starting_equity = self.session.starting_equity
            allocations = self.session.allocations
            session_id = self.session.session_id
            started_at = self.session.started_at
            session_state = self.session.state
        else:
            starting_equity = account_equity
            preview_a = round(account_equity / 3.0, 2) if account_equity else 0.0
            preview_b = round(account_equity / 3.0, 2) if account_equity else 0.0
            preview_c = round(account_equity - preview_a - preview_b, 2) if account_equity else 0.0
            allocations = {"A": preview_a, "B": preview_b, "C": preview_c}
            session_id = None
            started_at = None
            session_state = "READY"

        total_exposure = self._actual_exposure(self.last_positions)
        account_pnl = account_equity - starting_equity if starting_equity else 0.0

        models = {}
        for name, model in self.models.items():
            stats = summary.get(name, {})
            capital = float(allocations.get(name, model.capital if model.risk.session_capital else 0.0))
            if capital > 0 and model.risk.session_capital <= 0:
                model.risk.configure_session_capital(capital)

            open_positions = []
            open_exposure = 0.0
            for guard in model.guards.values():
                price = guard.current_price or guard.entry_price
                exposure = guard.quantity * price
                open_exposure += exposure
                open_positions.append(
                    {
                        "symbol": guard.symbol,
                        "trade_id": guard.trade_id,
                        "entry": guard.entry_price,
                        "current": price,
                        "qty": guard.quantity,
                        "stop": guard.stop_price,
                        "high": guard.high_watermark,
                        "unrealized_pl": guard.unrealized_pl,
                        "exposure": exposure,
                        "exit_pending": guard.exit_pending,
                    }
                )

            models[name] = {
                "name": name,
                "role": model.role,
                "label": model.label,
                "capital": capital,
                "equity": model.equity if capital else capital,
                "realized_pl": model.realized_pl,
                "unrealized_pl": model.unrealized_pl,
                "return_pct": model.return_pct if capital else 0.0,
                "drawdown_pct": model.drawdown_pct if capital else 0.0,
                "open_exposure": open_exposure,
                "open_positions": open_positions,
                "stats": stats,
                "risk": {
                    "risk_per_trade": model.risk.risk_budget if capital else 0.0,
                    "risk_per_trade_pct": float(model.risk.profile["risk_per_trade_pct"]) * 100,
                    "max_position": model.risk.max_position_notional if capital else 0.0,
                    "max_position_pct": float(model.risk.profile["max_position_pct"]) * 100,
                    "max_exposure": model.risk.max_exposure if capital else 0.0,
                    "max_exposure_pct": float(model.risk.profile["max_exposure_pct"]) * 100,
                    "daily_loss_limit": model.risk.daily_loss_limit if capital else 0.0,
                    "daily_loss_pct": float(model.risk.profile["daily_loss_pct"]) * 100,
                    "remaining_daily_loss": model.risk.remaining_daily_loss if capital else 0.0,
                    "max_trades": model.risk.max_trades_per_day,
                    "max_open_positions": model.risk.max_open_positions,
                    "consecutive_loss_limit": model.risk.max_consecutive_losses,
                },
                "signals": list(model.latest_signals.values()),
                "logs": list(model.logs),
            }

        if self.running:
            state = "DRAINING" if self.draining else ("PAUSED" if self.entries_paused else "RUNNING")
        else:
            state = session_state if session_state in {"FLATTENING", "FLATTENED"} else "STOPPED"

        return {
            "mode": "PAPER ONLY",
            "state": state,
            "running": self.running,
            "entries_paused": self.entries_paused,
            "draining": self.draining,
            "account": self.last_account,
            "experiment": {
                "session_id": session_id,
                "started_at": started_at,
                "starting_equity": starting_equity,
                "account_equity": account_equity,
                "account_pnl": account_pnl,
                "allocation_a": float(allocations.get("A", 0.0)),
                "allocation_b": float(allocations.get("B", 0.0)),
                "allocation_c": float(allocations.get("C", 0.0)),
                "split": "A/B/C equal thirds",
                "global_exposure": total_exposure,
                "global_exposure_limit": starting_equity * (
                    max(self.config.max_account_exposure_pct, 0.80)
                    if self.config.action_day_mode
                    else self.config.max_account_exposure_pct
                ) if starting_equity else 0.0,
                "action_day_mode": self.config.action_day_mode,
                "entry_window_open": self._entry_session(now),
                "market_time": now.isoformat(timespec="seconds"),
                "last_cycle_at": self.last_cycle_at.isoformat(timespec="seconds") if self.last_cycle_at else None,
                "last_cycle_error": self.last_cycle_error,
                "cycle_count": self.cycle_count,
            },
            "controls": {
                "can_start": not self.running,
                "can_pause": self.running and not self.entries_paused and not self.draining,
                "can_resume": self.running and self.entries_paused and not self.draining,
                "can_drain": self.running and not self.draining,
                "can_flatten": True,
            },
            "models": models,
            "model_c": self.model_c.status(
                execution_enabled=self.config.model_c_execution_enabled
                and self.config.model_c_enabled
            ),
            "universe": {
                "symbols": self.universe,
                "stats": self.universe_stats,
                "refreshed_at": self.universe_refreshed_at.isoformat() if self.universe_refreshed_at else None,
            },
            "trades": journal.get("trades", []),
            "trade_summary": journal.get("summary", {}),
        }
