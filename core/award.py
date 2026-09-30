"""Deterministic award scenarios. Used by the Award screen AND exposed to the analyst agent as a tool,
so the numbers the agent quotes are the same numbers the buyer sees on screen."""
import pandas as pd
from . import store
from .normalize import settings

def eligible_vendors(mode=None):
    v = store.read_sql("SELECT vendor_id, gate_status FROM vendors")
    mode = mode or settings()["gate_mode"]
    ok = {"CLEARED"} | ({"CONDITIONAL"} if mode == "lenient" else set())
    return v[v.gate_status.isin(ok)].vendor_id.tolist()

def scenario(vendors=None, strategy="line_wise_cheapest", single_vendor=None, include_estimates=None,
             include_unconfirmed=True, exclude_lines=None, gate_mode=None):
    s = settings()
    include_estimates = s["include_estimates_in_award"] if include_estimates is None else include_estimates
    if strategy == "single_vendor" and single_vendor:
        vendors = [single_vendor]
    vendors = vendors if vendors else eligible_vendors(gate_mode)
    q = store.read_sql("SELECT * FROM quotes WHERE usable=1")
    lines = store.read_sql("SELECT * FROM lines")
    q = q[q.vendor_id.isin(vendors)]
    notes = []
    if not include_estimates:
        dropped = q[q.is_estimate == 1]
        if len(dropped):
            notes.append(f"{len(dropped)} estimated/derived-incomplete prices excluded (toggle include_estimates to use them): "
                         + ", ".join(sorted({f'{r.vendor_id}' for r in dropped.itertuples()})))
        q = q[q.is_estimate == 0]
    if not include_unconfirmed:
        q = q[q.review_status != "needs_review"]
    if exclude_lines:
        q = q[~q.line_id.isin(exclude_lines)]; lines = lines[~lines.line_id.isin(exclude_lines)]
    q = q.sort_values(["line_id", "landed_inr_per_uom"])
    best = q.groupby("line_id").head(1)
    second = q.groupby("line_id").nth(1)[["line_id", "vendor_id", "landed_inr_per_uom"]].rename(
        columns={"vendor_id": "runner_up", "landed_inr_per_uom": "runner_up_landed"}) if len(q) else pd.DataFrame(columns=["line_id","runner_up","runner_up_landed"])
    award = lines[["line_id", "description", "annual_qty", "uom", "last_year_price_inr"]].merge(
        best[["line_id", "vendor_id", "landed_inr_per_uom", "price_inr_per_uom", "freight_inr_per_uom", "confidence", "review_status", "is_estimate", "flags"]],
        on="line_id", how="left").merge(second, on="line_id", how="left")
    award["annual_value_inr"] = (award.landed_inr_per_uom * award.annual_qty).round(0)
    award["last_year_value_inr"] = (award.last_year_price_inr * award.annual_qty).round(0)
    uncovered = award[award.vendor_id.isna()].line_id.tolist()
    if uncovered:
        notes.append(f"{len(uncovered)} line(s) have NO eligible price and are not in the total: {', '.join(uncovered)}")
    unconf = award[award.review_status == "needs_review"]
    if len(unconf):
        notes.append(f"{len(unconf)} awarded price(s) still need review: " + ", ".join(f"{r.line_id}/{r.vendor_id}" for r in unconf.itertuples()))
    by_v = award.dropna(subset=["vendor_id"]).groupby("vendor_id").agg(lines=("line_id", "count"), value_inr=("annual_value_inr", "sum")).reset_index()
    covered = award.dropna(subset=["vendor_id"])
    ly = covered.dropna(subset=["last_year_price_inr"])
    return {"vendors_considered": vendors, "strategy": strategy, "award": award, "by_vendor": by_v,
            "total_inr": float(covered.annual_value_inr.sum()), "lines_covered": int(len(covered)), "lines_total": int(len(lines)),
            "uncovered_lines": uncovered, "notes": notes,
            "like_for_like_vs_last_year": {"lines": int(len(ly)), "this_award_inr": float(ly.annual_value_inr.sum()),
                                           "last_year_inr": float(ly.last_year_value_inr.sum())}}

def scenario_summary(sc):
    """Compact JSON-able summary (for the LLM)."""
    a = sc["award"]
    return {"vendors_considered": sc["vendors_considered"], "strategy": sc["strategy"], "total_inr": round(sc["total_inr"]),
            "lines_covered": sc["lines_covered"], "lines_total": sc["lines_total"], "uncovered_lines": sc["uncovered_lines"],
            "by_vendor": sc["by_vendor"].to_dict("records"), "notes": sc["notes"], "like_for_like_vs_last_year": sc["like_for_like_vs_last_year"],
            "lines": a[["line_id", "vendor_id", "landed_inr_per_uom", "annual_value_inr", "runner_up", "runner_up_landed", "confidence", "review_status"]]
                     .round(2).where(a.notna(), None).to_dict("records")}
