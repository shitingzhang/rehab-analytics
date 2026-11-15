"""
08_figures.py - README figures that explain the sample. Run from the project root after 02_build_database.py:
    python src/08_figures.py
  Figure 1  reports/figures/sample_flow.png        from the PAC file to the analysis samples of v1.2 and v1.3
  Figure 2  reports/figures/missingness_pattern.png facilities with suppressed dual/rural values
Every number is computed from the data. Uses facility identifiers and the presence of a DTC rate only, no outcome values.
"""
import os

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from irf_common import imp_terms, load_facility, prepare  # noqa: E402

FIG_DIR = "reports/figures"
LINKAGE_PATH = "data/processed/linkage_audit/linkage_check.csv"
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE = "#2a78d6", "#eb6834"        # categorical slots 1 and 2 of the reference palette
Y = "i_019_02_dtc_rs_rate"
OTHER = ["therapy10", "group10", "cotreat5", "pt5", "slp5", "for_profit", "government", "physician", "unit", "risk",
         "age", "log_stays"]


def drops(s, steps):
    """Sequential exclusions: list of (label, n removed), and what is left."""
    out = []
    for lab, cols in steps:
        keep = s[cols].notna().all(axis=1)
        out.append((lab, int((~keep).sum())))
        s = s[keep]
    return out, s


def box(ax, x, y, w, h, text, edge=INK2, fill="white", weight="normal"):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0.01,rounding_size=0.015",
                                lw=1, ec=edge, fc=fill))
    ax.text(x, y, text, ha="center", va="center", fontsize=8.5, color=INK, weight=weight, linespacing=1.4)


def arrow(ax, x0, y0, x1, y1):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0), arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1))


def sample_flow(raw, d):
    pac_rows = len(pd.read_csv("data/raw/pac_irf_by_provider.csv", usecols=["SMRY_CTGRY"]))
    n_pac = len(raw)
    lk = pd.read_csv(LINKAGE_PATH, dtype={"ccn": str})
    n_exact, n_cross = int((lk["link"] == "exact CCN").sum()), int(lk["link"].str.startswith("crosswalk").sum())
    n_linked = len(lk)
    o = d[d[Y].notna()]
    n_out = len(o)
    imp = imp_terms(d)
    v12, s12 = drops(prepare(raw, pac_fill=None).loc[o.index],
                     [("rural % suppressed (*)", ["rural10"]), ("dual % suppressed (*) or withheld (+)", ["dual10"]),
                      ("therapy or other covariates missing", OTHER + imp + ["state"])])
    v13, s13 = drops(o, [("dual % withheld for Puerto Rico (+)", ["dual10"]),
                         ("therapy or other covariates missing", OTHER + imp + ["state", "dual_suppressed",
                                                                                 "rural_suppressed"])])
    n_filled = int(((s13["dual_suppressed"] + s13["rural_suppressed"]) > 0).sum())

    fig, ax = plt.subplots(figsize=(10, 7.6), facecolor=SURFACE)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    W, H, xc = 0.42, 0.075, 0.35
    rows = [(0.93, f"CMS Post-Acute Care utilization file, FY2024\n{pac_rows:,} rows"),
            (0.77, f"IRF providers\n{n_pac:,} facilities"),
            (0.61, f"Linked to Provider Data (quality, ownership)\n{n_linked:,} facilities "
                   f"({n_exact:,} exact CCN, {n_cross:,} via crosswalk)"),
            (0.45, f"With a discharge-to-community rate\n{n_out:,} facilities")]
    side = [(0.85, f"National and state summary rows\n{pac_rows - n_pac:,} excluded"),
            (0.69, f"No Provider Data record\n{n_pac - n_linked:,} excluded"),
            (0.53, f"No discharge-to-community rate\n{n_linked - n_out:,} excluded")]
    for y, t in rows:
        box(ax, xc, y, W, H, t)
    for (y0, _), (y1, _) in zip(rows, rows[1:]):
        arrow(ax, xc, y0 - H / 2, xc, y1 + H / 2)
    for y, t in side:
        box(ax, 0.82, y, 0.3, 0.06, t, fill="#f4f3f0")
        ax.plot([xc, 0.67], [y, y], color=INK2, lw=1)
    # the two versions
    xl, xr, yb, hb, Wb, yf, hf = 0.22, 0.7, 0.24, 0.15, 0.38, 0.06, 0.07
    for x in (xl, xr):
        arrow(ax, xc, 0.45 - H / 2, x, yb + hb / 2)
    t12 = "\n".join(f"− {n:,}  {lab}" for lab, n in v12)
    t13 = "\n".join(f"− {n:,}  {lab}" for lab, n in v13)
    box(ax, xl, yb, Wb, hb, f"As planned (v1.2): complete cases\n{t12}", edge=BLUE)
    box(ax, xr, yb, Wb, hb, f"Corrected (v1.3): '*' filled with 5 beneficiaries\n+ suppression indicator\n{t13}",
        edge=ORANGE)
    box(ax, xl, yf, Wb, hf, f"Analysis sample: {len(s12):,} facilities", edge=BLUE, weight="bold")
    box(ax, xr, yf, Wb, hf, f"Analysis sample: {len(s13):,} facilities\n({n_filled:,} with a filled value)",
        edge=ORANGE, weight="bold")
    for x in (xl, xr):
        arrow(ax, x, yb - hb / 2, x, yf + hf / 2)
    ax.set_title("Figure 1. From the CMS file to the analysis samples (discharge to community)", fontsize=11,
                 color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/sample_flow.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return len(s12), len(s13)


def missingness(d):
    o = d[d[Y].notna()].copy()
    o["dropped"] = ((o["dual_suppressed"] + o["rural_suppressed"]) > 0).astype(float)
    overall = 100 * o["dropped"].mean()
    q = pd.qcut(o["bene_dstnct_cnt"], 4)
    size_lab = [f"Q{i + 1}\n{int(iv.left) + 1}–{int(iv.right)}" for i, iv in enumerate(q.cat.categories)]
    panels = [
        ("Facility size\n(Medicare beneficiaries, quartiles)", q.cat.rename_categories(size_lab)),
        ("Setting", o["unit"].map({1.0: "Hospital-based\nunit", 0.0: "Free-standing"})),
        ("Ownership", o["ownership_type"].replace({"Physician": None})),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.6), facecolor=SURFACE, sharey=True,
                             gridspec_kw={"width_ratios": [4, 2, 3]})
    for ax, (title, g) in zip(axes, panels):
        t = o.assign(_g=g).dropna(subset=["_g"]).groupby("_g", observed=True)["dropped"].agg(["mean", "size"])
        x = range(len(t))
        ax.bar(x, 100 * t["mean"], width=0.55, color=ORANGE)
        ax.axhline(overall, color=INK2, lw=1, ls="--")
        for i, (m, n) in enumerate(zip(t["mean"], t["size"])):
            ax.text(i, 100 * m + 2, f"{100 * m:.0f}%", ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"{lab}\n(n = {n})" for lab, n in zip(t.index, t["size"])], fontsize=8, color=INK2)
        ax.set_title(title, fontsize=9.5, color=INK)
        ax.set_ylim(0, 100)
        ax.set_facecolor(SURFACE)
        ax.yaxis.grid(True, color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=INK2, length=0)
    axes[0].set_ylabel("% of facilities with a suppressed\ndual % or rural % value", fontsize=9, color=INK2)
    axes[0].text(3.35, overall + 2, f"all facilities: {overall:.0f}%", fontsize=8, color=INK2, ha="right", va="bottom")
    n_physician = int(o["ownership_type"].eq("Physician").sum())
    fig.suptitle("Figure 2. Suppressed dual/rural values: mostly smaller facilities and hospital-based units"
                 f"\n({int(o['dropped'].sum()):,} of {len(o):,} facilities with DTC; "
                 f"{n_physician} physician-owned omitted from ownership panel)",
                 fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(f"{FIG_DIR}/missingness_pattern.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    raw = load_facility()
    d = prepare(raw)
    n12, n13 = sample_flow(raw, d)
    missingness(d)
    print(f"Saved {FIG_DIR}/sample_flow.png (v1.2 n = {n12:,}, v1.3 n = {n13:,}) and {FIG_DIR}/missingness_pattern.png")


if __name__ == "__main__":
    main()
