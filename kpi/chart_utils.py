"""
Server-side chart generation using matplotlib.
Returns base64-encoded PNG strings suitable for embedding in HTML/PDF.
"""
import base64
import io
import matplotlib
matplotlib.use("Agg")  # headless backend
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

_PRIMARY   = "#1A4D3E"
_ACCENT    = "#2C7A5C"
_AMBER     = "#D97706"
_RED       = "#DC2626"
_LIGHT     = "#F7F8F5"
_GRID      = "#E5EBE5"
_TEXT      = "#1C1C1C"
_MUTED     = "#6B7280"


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=_LIGHT)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return encoded


def revenue_vs_expenses_chart(labels: list, revenue: list, expenses: list) -> str:
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor(_LIGHT)
    ax.set_facecolor(_LIGHT)
    x = range(len(labels))
    width = 0.38
    ax.bar([i - width / 2 for i in x], revenue, width, label="Revenue", color=_ACCENT, zorder=3)
    ax.bar([i + width / 2 for i in x], expenses, width, label="Expenses", color=_AMBER, zorder=3)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9, color=_MUTED)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"KES {v/1000:.0f}k" if v >= 1000 else f"KES {v:.0f}"))
    ax.tick_params(axis="y", colors=_MUTED, labelsize=9)
    ax.grid(axis="y", color=_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(frameon=False, fontsize=9, labelcolor=_MUTED)
    ax.set_title("Revenue vs Expenses", color=_TEXT, fontsize=12, fontweight="bold", pad=10)
    return _fig_to_b64(fig)


def cumulative_profit_chart(labels: list, profit: list) -> str:
    fig, ax = plt.subplots(figsize=(8, 3.5))
    fig.patch.set_facecolor(_LIGHT)
    ax.set_facecolor(_LIGHT)
    x = range(len(labels))
    ax.fill_between(list(x), profit, alpha=0.18, color=_PRIMARY)
    ax.plot(list(x), profit, color=_PRIMARY, linewidth=2.5, marker="o", markersize=5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9, color=_MUTED)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"KES {v/1000:.0f}k" if abs(v) >= 1000 else f"KES {v:.0f}"))
    ax.tick_params(axis="y", colors=_MUTED, labelsize=9)
    ax.grid(axis="y", color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("Cumulative Net Income", color=_TEXT, fontsize=12, fontweight="bold", pad=10)
    return _fig_to_b64(fig)


def expense_mix_chart(labels: list, values: list) -> str:
    colors = [_PRIMARY, _ACCENT, "#5E907A", "#8DB39F", _AMBER, "#F59E0B",
              "#FCD34D", _RED, _MUTED, "#D1D9D1"]
    fig, ax = plt.subplots(figsize=(6, 5))
    fig.patch.set_facecolor(_LIGHT)
    ax.set_facecolor(_LIGHT)
    wedges, texts, autotexts = ax.pie(
        values, labels=None, autopct="%1.1f%%",
        colors=colors[:len(values)], startangle=140,
        wedgeprops={"linewidth": 0}, pctdistance=0.8,
    )
    for autotext in autotexts:
        autotext.set_fontsize(8)
        autotext.set_color("white")
    ax.legend(wedges, [l[:20] for l in labels], loc="lower center",
              bbox_to_anchor=(0.5, -0.12), ncol=2, frameon=False,
              fontsize=8, labelcolor=_MUTED)
    ax.set_title("Expense Mix", color=_TEXT, fontsize=12, fontweight="bold", pad=10)
    return _fig_to_b64(fig)


def kpi_scorecard_chart(kpi_names: list, kpi_values: list, kpi_statuses: list) -> str:
    STATUS_COLOR = {"HEALTHY": _ACCENT, "WARNING": _AMBER, "CRITICAL": _RED}
    colors = [STATUS_COLOR.get(s, _MUTED) for s in kpi_statuses]
    fig, ax = plt.subplots(figsize=(8, max(3, len(kpi_names) * 0.6)))
    fig.patch.set_facecolor(_LIGHT)
    ax.set_facecolor(_LIGHT)
    y = range(len(kpi_names))
    ax.barh(list(y), kpi_values, color=colors, height=0.55, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(kpi_names, fontsize=9, color=_TEXT)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
    ax.tick_params(axis="x", colors=_MUTED, labelsize=8)
    ax.grid(axis="x", color=_GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("KPI Scorecard", color=_TEXT, fontsize=12, fontweight="bold", pad=10)
    ax.invert_yaxis()
    return _fig_to_b64(fig)
