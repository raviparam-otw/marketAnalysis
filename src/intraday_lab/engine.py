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
from .strategy import OpeningRangeVwapStrategy


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
        self.entries_by_symbol: dict[str, int] = {}
        self.last_exit_at: dict[str, datetime] = {}

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
    """Paper-only two-model experiment with frozen daily A/C virtual allocations."""

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
                "AGGRESSIVE RULES",
                "Opening Range / VWAP Momentum",
                OpeningRangeVwapStrategy(
                    relative_volume_min=config.relative_volume_min,
                    breakout_buffer_pct=config.model_a_breakout_buffer_pct,
                    confirmation_bars=config.model_a_confirmation_bars,
                    max_extension_from_or_pct=config.model_a_max_extension_from_or_pct,
                    max_extension_from_vwap_pct=config.model_a_max_extension_from_vwap_pct,
                    max_bar_age_seconds=config.model_a_max_bar_age_seconds,
                ),
                RiskManager(config, "A"),
            ),
            "C": ModelRuntime(
                "C",
                "AGGRESSIVE AI",
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
    def _is_two_way_session(session: ExperimentSession | None) -> bool:
        if session is None:
            return False
        allocations = session.allocations
        return (
            set(allocations) == {"A", "C"}
            and all(float(allocations.get(name, 0.0)) > 0 for name in ("A", "C"))
        )

    def upgrade_session_if_flat(
        self,
        *,
        account: dict,
        positions: list,
        open_orders: list,
        now: datetime | None = None,
    ) -> bool:
        """Migrate a same-day legacy session to A/C only when the paper account is flat."""
        current = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        existing = self.session_store.load_today(current)
        if existing is None or self._is_two_way_session(existing):
            self.session = existing
            return False
        if positions or open_orders:
            return False

        self.session = self.session_store.replace_with_two_way(
            float(account["equity"]),
            current,
        )
        self._configure_allocations(self.session)
        for model in self.models.values():
            model.log(
                "CONTROL",
                "Legacy session migrated to equal A/C split",
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

        migrated = self.upgrade_session_if_flat(
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
                "Cannot create a clean A/C session while pre-existing PAPER positions exist "
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
                    f"Existing PAPER position {symbol} is not attributable to Model A/C. "
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
                entry_order_id=str(row.get("order_id") or "") or None,
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

        guard_symbols = list({
            symbol
            for model in self.models.values()
            for symbol in model.guards
        })
        guard_bars = (
            await asyncio.to_thread(self.broker.minute_bars, guard_symbols, 1)
            if guard_symbols
            else {}
        )

        self.last_account = account
        self.last_positions = positions
        position_map = {str(position.symbol).upper(): position for position in positions}

        for model in self.models.values():
            await self._manage_model_position(model, position_map, now, guard_bars)

        # A guard remains until Alpaca no longer reports the position.
        for model in self.models.values():
            for symbol, guard in list(model.guards.items()):
                position = position_map.get(symbol)
                if position is None and guard.exit_pending:
                    model.guards.pop(symbol, None)
                    if model.name == "A":
                        model.last_exit_at[symbol] = now
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
        market_aligned = self._market_alignment(bars, now)
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
                    if model.name == "A":
                        if model.entries_by_symbol.get(symbol, 0) >= self.config.model_a_max_entries_per_symbol:
                            rejection_counts["symbol entry cap"] = rejection_counts.get("symbol entry cap", 0) + 1
                            continue
                        last_exit = model.last_exit_at.get(symbol)
                        if (
                            last_exit is not None
                            and now - last_exit < timedelta(minutes=self.config.model_a_reentry_cooldown_minutes)
                        ):
                            rejection_counts["reentry cooldown"] = rejection_counts.get("reentry cooldown", 0) + 1
                            continue
                    catalyst = None
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
                        rejected_by=rejection_counts,
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

                execution_price = candidate.price
                execution_context = {}
                if model.name == "A":
                    try:
                        quote = await asyncio.to_thread(self.broker.latest_quote, candidate.symbol)
                    except Exception as exc:
                        model.log("DATA", "Entry rejected: quote lookup failed", symbol=candidate.symbol, error=str(exc))
                        continue

                    bid = float(quote.get("bid", 0.0) or 0.0)
                    ask = float(quote.get("ask", 0.0) or 0.0)
                    spread_pct = quote.get("spread_pct")
                    timestamp = quote.get("timestamp")
                    if bid <= 0 or ask <= bid or spread_pct is None:
                        model.log("DATA", "Entry rejected: invalid quote", symbol=candidate.symbol)
                        continue

                    quote_age_seconds = 0.0
                    if timestamp is not None:
                        quote_time = timestamp
                        if quote_time.tzinfo is None:
                            quote_time = quote_time.replace(tzinfo=EASTERN)
                        quote_age_seconds = max(
                            0.0,
                            (now.astimezone(quote_time.tzinfo) - quote_time).total_seconds(),
                        )
                    if quote_age_seconds > self.config.model_a_quote_max_age_seconds:
                        model.log(
                            "DATA",
                            "Entry rejected: stale quote",
                            symbol=candidate.symbol,
                            quote_age_seconds=round(quote_age_seconds, 3),
                        )
                        continue
                    if float(spread_pct) > self.config.model_a_max_entry_spread_pct:
                        model.log(
                            "DATA",
                            "Entry rejected: spread too wide",
                            symbol=candidate.symbol,
                            spread_pct=round(float(spread_pct) * 100, 3),
                        )
                        continue
                    max_ask = candidate.price * (1 + self.config.model_a_max_entry_slippage_pct)
                    if ask > max_ask:
                        model.log(
                            "DATA",
                            "Entry rejected: price moved beyond slippage cap",
                            symbol=candidate.symbol,
                            signal_price=round(candidate.price, 4),
                            ask=round(ask, 4),
                            max_ask=round(max_ask, 4),
                        )
                        continue

                    execution_price = max_ask
                    execution_context = {
                        "entry_quote": {
                            "bid": bid,
                            "ask": ask,
                            "spread_pct": float(spread_pct),
                            "quote_age_seconds": quote_age_seconds,
                            "feed": quote.get("feed"),
                        }
                    }

                model_exposure = sum(
                    guard.quantity * (guard.current_price or guard.entry_price)
                    for guard in model.guards.values()
                )
                model_room = max(0.0, model.risk.max_exposure - model_exposure)
                check = model.risk.entry_check(
                    execution_price,
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
                order_qty = int(float(check.quantity)) if model.name == "A" else check.quantity
                if order_qty <= 0:
                    model.log(
                        "RISK",
                        "Entry rejected",
                        reason="protected order requires at least 1 whole share",
                        symbol=candidate.symbol,
                        calculated_qty=check.quantity,
                    )
                    continue

                if model.name == "A":
                    order = await asyncio.to_thread(
                        self.broker.buy_protected_limit_qty,
                        candidate.symbol,
                        order_qty,
                        execution_price,
                        candidate.stop_price,
                        client_order_id,
                    )
                else:
                    order = await asyncio.to_thread(
                        self.broker.buy_qty,
                        candidate.symbol,
                        order_qty,
                        client_order_id,
                    )

                actual_notional = float(order_qty) * execution_price
                actual_risk_dollars = float(order_qty) * max(0.0, execution_price - candidate.stop_price)
                profile = model.risk.profile
                context = candidate.to_dict() | execution_context | {
                    "risk_dollars": round(actual_risk_dollars, 2),
                    "planned_notional": round(actual_notional, 2),
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
                    requested_qty=order_qty,
                    signal_price=candidate.price,
                    signal_context=context,
                )

                model.guards[candidate.symbol] = PositionGuard(
                    model=model.name,
                    symbol=candidate.symbol,
                    trade_id=trade_id,
                    entry_price=execution_price,
                    quantity=order_qty,
                    high_watermark=candidate.price,
                    stop_price=candidate.stop_price,
                    entered_at=now,
                    current_price=execution_price,
                    entry_order_id=str(order.id),
                )
                if model.name == "A":
                    model.entries_by_symbol[candidate.symbol] = model.entries_by_symbol.get(candidate.symbol, 0) + 1
                locked.add(candidate.symbol)
                available_cash = max(0.0, available_cash - actual_notional)
                global_room = max(0.0, global_room - actual_notional)
                entries_this_cycle += 1
                model.log(
                    "TRADE",
                    "Paper buy submitted",
                    symbol=candidate.symbol,
                    qty=round(float(order_qty), 4),
                    notional=round(actual_notional, 2),
                    risk=round(actual_risk_dollars, 2),
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
        missing = [
            name
            for name, state in stack_status.items()
            if state.get("enabled") and not state.get("ready")
        ]
        if missing and (
            model.last_scan_log_at is None
            or now - model.last_scan_log_at >= timedelta(seconds=60)
        ):
            model.log(
                "WARN",
                "Model C auxiliary intelligence degraded; Fin-R1 remains active",
                missing=missing,
                stack=stack_status,
            )

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
                    "WARN",
                    "Model C continuing with partial intelligence",
                    errors=enrichment_errors,
                )

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

        selected, execution_rejections = self.model_c.signal_from_decision(
            shortlist,
            decision,
            now=now,
            market_aligned=market_aligned,
            min_confidence=self.config.model_c_min_confidence,
            min_rvol=self.config.model_c_min_rvol,
            require_positive_1m=self.config.model_c_require_positive_1m,
            require_vwap_or_positive_5m=self.config.model_c_require_vwap_or_positive_5m,
            max_distance_from_hod_pct=self.config.model_c_max_distance_from_hod_pct,
            max_extension_from_vwap_pct=self.config.model_c_max_extension_from_vwap_pct,
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
            if execution_rejections:
                model.log(
                    "CONTROL",
                    "Model C BUY rejected by deterministic validator",
                    symbol=decision.get("symbol"),
                    confidence=round(confidence, 4),
                    reasons=execution_rejections,
                )
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
        bars_by_symbol: dict | None = None,
    ) -> None:
        for symbol, guard in list(model.guards.items()):
            position = position_map.get(symbol)
            if position is None:
                if model.name == "A" and guard.entry_order_id:
                    try:
                        parent = await asyncio.to_thread(
                            self.broker.order_nested,
                            guard.entry_order_id,
                        )
                        parent_status = str(
                            getattr(
                                getattr(parent, "status", None),
                                "value",
                                getattr(parent, "status", ""),
                            )
                        ).lower()
                        stop_leg = None
                        for leg in list(getattr(parent, "legs", None) or []):
                            side = str(
                                getattr(
                                    getattr(leg, "side", None),
                                    "value",
                                    getattr(leg, "side", ""),
                                )
                            ).lower()
                            if side == "sell" and getattr(leg, "stop_price", None) not in (None, ""):
                                stop_leg = leg
                                guard.protective_stop_order_id = str(leg.id)
                                break

                        if stop_leg is not None:
                            stop_status = str(
                                getattr(
                                    getattr(stop_leg, "status", None),
                                    "value",
                                    getattr(stop_leg, "status", ""),
                                )
                            ).lower()
                            if stop_status == "filled":
                                self.journal.record_order(
                                    stop_leg,
                                    model=model.name,
                                    side="SELL",
                                    symbol=symbol,
                                    reason="broker_protective_stop",
                                    trade_id=guard.trade_id,
                                    session_id=self.session.session_id if self.session else None,
                                    requested_qty=guard.quantity,
                                    signal_price=float(getattr(stop_leg, "filled_avg_price", 0) or guard.stop_price),
                                    signal_context={
                                        "entry": guard.entry_price,
                                        "stop": guard.stop_price,
                                        "high_watermark": guard.high_watermark,
                                    },
                                )
                                guard.exit_pending = True
                                model.log(
                                    "TRADE",
                                    "Broker protective stop filled",
                                    symbol=symbol,
                                    stop_order_id=str(stop_leg.id),
                                )
                                continue

                        terminal = {"canceled", "expired", "rejected", "replaced", "done_for_day"}
                        elapsed = max(0.0, (now - guard.entered_at).total_seconds())
                        if parent_status in terminal:
                            model.guards.pop(symbol, None)
                            model.log(
                                "TRADE",
                                "Unfilled Model A entry cleared",
                                symbol=symbol,
                                status=parent_status,
                            )
                        elif parent_status != "filled" and elapsed >= self.config.model_a_entry_timeout_seconds:
                            try:
                                await asyncio.to_thread(
                                    self.broker.cancel_order,
                                    guard.entry_order_id,
                                )
                                model.log(
                                    "TRADE",
                                    "Stale Model A entry canceled",
                                    symbol=symbol,
                                    age_seconds=round(elapsed, 1),
                                )
                            finally:
                                model.guards.pop(symbol, None)
                    except Exception as exc:
                        model.log(
                            "WARN",
                            "Model A pending-entry status check failed",
                            symbol=symbol,
                            error=str(exc),
                        )
                continue

            current = float(position.current_price)
            entry = float(position.avg_entry_price)
            guard.entry_price = entry
            guard.current_price = current
            guard.unrealized_pl = float(position.unrealized_pl)
            observed_high = current
            frame = (bars_by_symbol or {}).get(symbol)
            if frame is not None and not frame.empty and "high" in frame:
                observed_high = max(observed_high, float(frame.tail(2)["high"].max()))
            guard.high_watermark = max(guard.high_watermark, observed_high)

            if (
                model.name == "A"
                and guard.entry_order_id
                and not guard.protective_stop_order_id
            ):
                try:
                    parent = await asyncio.to_thread(
                        self.broker.order_nested,
                        guard.entry_order_id,
                    )
                    for leg in list(getattr(parent, "legs", None) or []):
                        side = str(getattr(getattr(leg, "side", None), "value", getattr(leg, "side", ""))).lower()
                        if side == "sell" and getattr(leg, "stop_price", None) not in (None, ""):
                            guard.protective_stop_order_id = str(leg.id)
                            model.log(
                                "RISK",
                                "Broker protective stop confirmed",
                                symbol=symbol,
                                stop_order_id=guard.protective_stop_order_id,
                                stop_price=float(leg.stop_price),
                            )
                            break
                except Exception as exc:
                    model.log(
                        "WARN",
                        "Protective stop lookup failed",
                        symbol=symbol,
                        error=str(exc),
                    )

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

            if model.name == "A" and guard.protective_stop_order_id:
                stop_id = guard.protective_stop_order_id
                try:
                    await asyncio.to_thread(self.broker.cancel_order, stop_id)
                except Exception as exc:
                    model.log(
                        "WARN",
                        "Protective stop cancel request failed",
                        symbol=symbol,
                        stop_order_id=stop_id,
                        error=str(exc),
                    )

                try:
                    stop_order = await asyncio.to_thread(self.broker.order, stop_id)
                    stop_status = str(
                        getattr(
                            getattr(stop_order, "status", None),
                            "value",
                            getattr(stop_order, "status", ""),
                        )
                    ).lower()
                except Exception as exc:
                    model.log(
                        "WARN",
                        "Protective stop status unavailable; deferring software exit",
                        symbol=symbol,
                        stop_order_id=stop_id,
                        error=str(exc),
                    )
                    continue

                if stop_status == "filled":
                    self.journal.record_order(
                        stop_order,
                        model=model.name,
                        side="SELL",
                        symbol=symbol,
                        reason="broker_protective_stop",
                        trade_id=guard.trade_id,
                        session_id=self.session.session_id if self.session else None,
                        requested_qty=guard.quantity,
                        signal_price=float(getattr(stop_order, "filled_avg_price", 0) or guard.stop_price),
                        signal_context={
                            "entry": entry,
                            "stop": guard.stop_price,
                            "high_watermark": guard.high_watermark,
                        },
                    )
                    guard.exit_pending = True
                    model.log(
                        "TRADE",
                        "Broker protective stop filled",
                        symbol=symbol,
                        stop_order_id=stop_id,
                    )
                    continue
                if stop_status not in {"canceled", "expired", "rejected", "replaced", "done_for_day"}:
                    model.log(
                        "WARN",
                        "Protective stop still active; deferring software exit",
                        symbol=symbol,
                        stop_order_id=stop_id,
                        status=stop_status,
                    )
                    continue
                guard.protective_stop_order_id = None

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

        journal_status = self.journal.status()
        summary = journal_status.get("summary", {}).get("models", {})

        # Rebuild Model A's per-symbol entry/cooldown state from filled journal
        # records so a process restart cannot bypass the re-entry controls.
        model_a = self.models.get("A")
        if model_a is not None:
            entries_by_symbol: dict[str, int] = {}
            last_exit_at: dict[str, datetime] = {}
            for row in journal_status.get("trades", []):
                if row.get("model") != "A" or row.get("status") != "filled":
                    continue
                symbol = str(row.get("symbol", "")).upper()
                if not symbol:
                    continue
                side = str(row.get("side", "")).upper()
                if side == "BUY":
                    entries_by_symbol[symbol] = entries_by_symbol.get(symbol, 0) + 1
                elif side == "SELL":
                    raw = row.get("filled_at") or row.get("recorded_at")
                    try:
                        closed_at = datetime.fromisoformat(str(raw))
                        if closed_at.tzinfo is None:
                            closed_at = closed_at.replace(tzinfo=EASTERN)
                        last_exit_at[symbol] = closed_at.astimezone(EASTERN)
                    except Exception:
                        pass
            model_a.entries_by_symbol = entries_by_symbol
            model_a.last_exit_at = last_exit_at

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
    def _market_alignment(bars: dict, now: datetime | None = None) -> bool:
        current_time = (now or datetime.now(EASTERN)).astimezone(EASTERN)
        aligned = 0
        observed = 0
        for benchmark in ("SPY", "QQQ"):
            frame = bars.get(benchmark)
            if frame is None or len(frame) < 6:
                continue

            recent = frame.copy().sort_index()
            index = recent.index
            if index.tz is None:
                index = index.tz_localize("UTC")
            recent.index = index.tz_convert(EASTERN)
            recent = recent[recent.index.date == current_time.date()].tail(30)
            if len(recent) < 6:
                continue

            observed += 1
            typical = (recent["high"] + recent["low"] + recent["close"]) / 3
            cumulative_volume = recent["volume"].cumsum().replace(0, float("nan"))
            vwap = float(((typical * recent["volume"]).cumsum() / cumulative_volume).iloc[-1])
            current = float(recent.iloc[-1]["close"])
            five_minute_reference = float(recent.iloc[-6]["close"])
            if current > vwap and current > five_minute_reference:
                aligned += 1

        return observed > 0 and aligned >= 1

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
            preview_a = round(account_equity / 2.0, 2) if account_equity else 0.0
            preview_c = round(account_equity - preview_a, 2) if account_equity else 0.0
            allocations = {"A": preview_a, "C": preview_c}
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
                "allocation_c": float(allocations.get("C", 0.0)),
                "split": "A/C equal halves",
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
