import copy
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict

from src.config import AppConfig, CONFIG
from src.backtest.evaluator import SignalOutcomeEvaluator
from src.utils.logger import get_logger

logger = get_logger(__name__)

@dataclass
class DatasetSplit:
    """
    Chronological dataset separation per PRD §37.
    Strictly chronological: TRAIN -> VALIDATION -> UNSEEN TEST.
    Random shuffling across time series is strictly prohibited.
    """
    train_timestamps: List[Any]
    validation_timestamps: List[Any]
    test_timestamps: List[Any]

    def validate_chronology(self) -> bool:
        """Verifies that splits are strictly chronological and non-overlapping."""
        if not self.train_timestamps or not self.validation_timestamps:
            return True
        max_train = max(self.train_timestamps)
        min_val = min(self.validation_timestamps)
        if max_train >= min_val:
            raise ValueError(f"Train/Validation overlap or inverted chronology: max_train={max_train} >= min_val={min_val}")
        if self.test_timestamps:
            max_val = max(self.validation_timestamps)
            min_test = min(self.test_timestamps)
            if max_val >= min_test:
                raise ValueError(f"Validation/Test overlap or inverted chronology: max_val={max_val} >= min_test={min_test}")
        return True

@dataclass
class PerformanceMetrics:
    signals_total: int
    signals_per_day: float
    entry_rate: float
    tp1_hit_rate: float
    tp2_hit_rate: float
    tp3_hit_rate: float
    sl_rate: float
    win_rate: float
    average_r: float
    expected_r: float
    profit_factor: float
    max_drawdown_r: float
    worst_losing_streak: int
    avg_holding_bars: float

class ThresholdTuner:
    """
    Threshold Research & Anti-Overfitting Calibration Tooling
    per PRD §33-§38 and Milestone 6.3.
    Tunes only existing configuration parameters without introducing indicator bloat.
    """

    def __init__(self, evaluator: Optional[SignalOutcomeEvaluator] = None):
        self.evaluator = evaluator or SignalOutcomeEvaluator()

    def calculate_metrics(
        self,
        evaluations: List[Dict[str, Any]],
        num_days: float = 1.0,
    ) -> PerformanceMetrics:
        """Computes comprehensive performance and risk-adjusted metrics from signal outcomes."""
        n_total = len(evaluations)
        if n_total == 0:
            return PerformanceMetrics(
                signals_total=0,
                signals_per_day=0.0,
                entry_rate=0.0,
                tp1_hit_rate=0.0,
                tp2_hit_rate=0.0,
                tp3_hit_rate=0.0,
                sl_rate=0.0,
                win_rate=0.0,
                average_r=0.0,
                expected_r=0.0,
                profit_factor=0.0,
                max_drawdown_r=0.0,
                worst_losing_streak=0,
                avg_holding_bars=0.0,
            )

        entered = [e for e in evaluations if e.get("entry_triggered", False)]
        n_entered = len(entered)
        entry_rate = round(n_entered / n_total, 3)

        if n_entered == 0:
            return PerformanceMetrics(
                signals_total=n_total,
                signals_per_day=round(n_total / max(1.0, num_days), 2),
                entry_rate=0.0,
                tp1_hit_rate=0.0,
                tp2_hit_rate=0.0,
                tp3_hit_rate=0.0,
                sl_rate=0.0,
                win_rate=0.0,
                average_r=0.0,
                expected_r=0.0,
                profit_factor=0.0,
                max_drawdown_r=0.0,
                worst_losing_streak=0,
                avg_holding_bars=0.0,
            )

        tp1_hits = sum(1 for e in entered if e.get("tp1_hit", False))
        tp2_hits = sum(1 for e in entered if e.get("tp2_hit", False))
        tp3_hits = sum(1 for e in entered if e.get("tp3_hit", False))
        sl_hits = sum(1 for e in entered if e.get("sl_hit", False))

        wins = [e for e in entered if e.get("net_realized_r", 0) > 0]
        losses = [e for e in entered if e.get("net_realized_r", 0) < 0]

        win_rate = round(len(wins) / n_entered, 3)
        total_r = sum(e.get("net_realized_r", 0) for e in entered)
        avg_r = round(total_r / n_entered, 3)

        # Expected R = (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
        avg_win_r = (sum(e.get("net_realized_r", 0) for e in wins) / len(wins)) if wins else 0.0
        avg_loss_r = (abs(sum(e.get("net_realized_r", 0) for e in losses)) / len(losses)) if losses else 0.0
        expected_r = round((win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r), 3)

        # Profit Factor = Sum of Gains / Sum of Losses
        sum_gains = sum(e.get("net_realized_r", 0) for e in wins)
        sum_losses = abs(sum(e.get("net_realized_r", 0) for e in losses))
        if sum_losses > 0:
            profit_factor = round(sum_gains / sum_losses, 2)
        else:
            profit_factor = 99.0 if sum_gains > 0 else 0.0

        # Drawdown and Losing Streak
        cumulative_r = 0.0
        peak_r = 0.0
        max_dd = 0.0
        current_losing_streak = 0
        worst_losing_streak = 0

        for e in entered:
            r = e.get("net_realized_r", 0)
            cumulative_r += r
            if cumulative_r > peak_r:
                peak_r = cumulative_r
            dd = peak_r - cumulative_r
            if dd > max_dd:
                max_dd = dd

            if r <= 0:
                current_losing_streak += 1
                if current_losing_streak > worst_losing_streak:
                    worst_losing_streak = current_losing_streak
            else:
                current_losing_streak = 0

        avg_holding = round(
            sum(e.get("holding_bars", 0) for e in entered) / n_entered, 1
        )

        return PerformanceMetrics(
            signals_total=n_total,
            signals_per_day=round(n_total / max(1.0, num_days), 2),
            entry_rate=entry_rate,
            tp1_hit_rate=round(tp1_hits / n_entered, 3),
            tp2_hit_rate=round(tp2_hits / n_entered, 3),
            tp3_hit_rate=round(tp3_hits / n_entered, 3),
            sl_rate=round(sl_hits / n_entered, 3),
            win_rate=win_rate,
            average_r=avg_r,
            expected_r=expected_r,
            profit_factor=profit_factor,
            max_drawdown_r=round(max_dd, 2),
            worst_losing_streak=worst_losing_streak,
            avg_holding_bars=avg_holding,
        )

    def evaluate_config(
        self,
        config: AppConfig,
        pipeline_runner,  # Callable accepting (config, timestamps) -> List[Signal]
        timestamps: List[Any],
        subsequent_data_provider,  # Callable accepting (signal) -> List[Kline]
        num_days: float = 1.0,
    ) -> PerformanceMetrics:
        """Evaluates a single configuration across a specific historical partition."""
        signals = pipeline_runner(config, timestamps)
        evaluations = []
        for sig in signals:
            klines = subsequent_data_provider(sig)
            ev = self.evaluator.evaluate_signal(sig, klines)
            evaluations.append(ev)

        return self.calculate_metrics(evaluations, num_days=num_days)

    def compare_configurations(
        self,
        baseline_metrics: PerformanceMetrics,
        candidate_metrics: PerformanceMetrics,
        baseline_name: str = "Baseline",
        candidate_name: str = "Candidate",
    ) -> str:
        """Generates a comparison Markdown table between baseline and candidate configurations."""
        b = asdict(baseline_metrics)
        c = asdict(candidate_metrics)

        lines = [
            f"# Configuration Comparison: {baseline_name} vs {candidate_name}",
            "",
            "| Metric | Baseline | Candidate | Delta |",
            "| :--- | :--- | :--- | :--- |",
        ]

        for k in b.keys():
            b_val = b[k]
            c_val = c[k]
            if isinstance(b_val, float):
                diff = round(c_val - b_val, 3)
                delta_str = f"+{diff}" if diff > 0 else f"{diff}"
            elif isinstance(b_val, int):
                diff = c_val - b_val
                delta_str = f"+{diff}" if diff > 0 else f"{diff}"
            else:
                delta_str = "-"
            lines.append(f"| **{k}** | {b_val} | {c_val} | {delta_str} |")

        return "\n".join(lines)

    def parameter_sweep(
        self,
        base_config: AppConfig,
        param_grid: Dict[str, List[Any]],
        evaluations_provider,  # Callable(config) -> List[evaluation_dicts]
        num_days: float = 1.0,
        sort_by: str = "expected_r",
    ) -> List[Dict[str, Any]]:
        """
        Runs a grid search over existing config parameters.

        param_grid keys use dot-notation to address nested config fields, e.g.:
            "volume.min_ratio"    -> base_config.volume.min_ratio
            "oi.min_change_pct"  -> base_config.oi.min_change_pct
            "risk.min_rr_tp1"    -> base_config.risk.min_rr_tp1

        Returns a list of result dicts sorted by sort_by metric (descending),
        so the top entry is the best-performing parameter combination on the
        provided dataset partition.

        Anti-overfitting note: always run sweep on TRAIN split only.
        Use the returned best config to evaluate on VALIDATION, then UNSEEN TEST
        exactly once — never re-sweep on those partitions.
        """
        import itertools

        keys = list(param_grid.keys())
        value_lists = [param_grid[k] for k in keys]
        results = []

        for combo in itertools.product(*value_lists):
            candidate_config = copy.deepcopy(base_config)

            # Apply each param via dot-notation
            for key, value in zip(keys, combo):
                parts = key.split(".")
                if len(parts) != 2:
                    raise ValueError(
                        f"param_grid key must be 'section.field', got: '{key}'"
                    )
                section, field = parts
                section_obj = getattr(candidate_config, section, None)
                if section_obj is None:
                    raise ValueError(f"Config has no section '{section}'")
                if not hasattr(section_obj, field):
                    raise ValueError(f"Config section '{section}' has no field '{field}'")
                setattr(section_obj, field, value)

            param_label = ", ".join(f"{k}={v}" for k, v in zip(keys, combo))
            logger.info(f"[Sweep] Evaluating: {param_label}")

            try:
                evaluations = evaluations_provider(candidate_config)
                metrics = self.calculate_metrics(evaluations, num_days=num_days)
                result_row = asdict(metrics)
                result_row["params"] = dict(zip(keys, combo))
                result_row["param_label"] = param_label
                results.append(result_row)
            except Exception as e:
                logger.warning(f"[Sweep] Skipping {param_label} due to error: {e}")

        results.sort(key=lambda r: r.get(sort_by, float("-inf")), reverse=True)
        logger.info(f"[Sweep] Complete. {len(results)} combinations evaluated.")
        return results

    def sweep_report_markdown(
        self,
        sweep_results: List[Dict[str, Any]],
        title: str = "Parameter Sweep Report",
    ) -> str:
        """
        Converts sweep results to a readable Markdown comparison table.
        Each row is one parameter combination. Top row = best combination.
        """
        if not sweep_results:
            return f"# {title}\n\nNo results to display."

        metric_cols = [
            "signals_total", "signals_per_day", "entry_rate",
            "tp1_hit_rate", "tp2_hit_rate", "tp3_hit_rate", "sl_rate",
            "win_rate", "average_r", "expected_r", "profit_factor",
            "max_drawdown_r", "worst_losing_streak", "avg_holding_bars",
        ]

        header = "| Rank | Parameters | " + " | ".join(metric_cols) + " |"
        separator = "| :--- | :--- | " + " | ".join([":---:"] * len(metric_cols)) + " |"

        lines = [f"# {title}", "", header, separator]
        for rank, row in enumerate(sweep_results, start=1):
            param_str = row.get("param_label", "—")
            col_vals = " | ".join(str(row.get(m, "—")) for m in metric_cols)
            lines.append(f"| {rank} | {param_str} | {col_vals} |")

        return "\n".join(lines)

    def walk_forward_evaluate(
        self,
        all_evaluations: List[Dict[str, Any]],
        n_windows: int = 3,
        train_ratio: float = 0.6,
        num_days_per_window: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """
        Splits evaluations chronologically into n_windows rolling folds and
        computes metrics for each fold's train and test partitions.

        Strictly chronological — no shuffling.
        Returns a list of dicts with 'window', 'train_metrics', 'test_metrics'.
        """
        n = len(all_evaluations)
        window_size = n // n_windows
        if window_size < 2:
            logger.warning("[WalkForward] Too few evaluations for walk-forward.")
            return []

        window_results = []
        for w in range(n_windows):
            start = w * window_size
            end = start + window_size if w < n_windows - 1 else n
            window_evals = all_evaluations[start:end]

            split_idx = int(len(window_evals) * train_ratio)
            train_evals = window_evals[:split_idx]
            test_evals = window_evals[split_idx:]

            train_days = num_days_per_window * train_ratio
            test_days = num_days_per_window * (1 - train_ratio)

            train_metrics = self.calculate_metrics(train_evals, num_days=max(1.0, train_days))
            test_metrics = self.calculate_metrics(test_evals, num_days=max(1.0, test_days))

            window_results.append({
                "window": w + 1,
                "train_n": len(train_evals),
                "test_n": len(test_evals),
                "train_metrics": asdict(train_metrics),
                "test_metrics": asdict(test_metrics),
            })

        logger.info(f"[WalkForward] Completed {n_windows} windows.")
        return window_results
