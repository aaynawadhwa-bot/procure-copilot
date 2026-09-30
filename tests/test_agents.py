"""Tests the agent loops' plumbing (tool dispatch, award engine, exports) with a scripted fake LLM client.
The fake only replays tool calls - it proves the loop, SQL guard, and exports work; real reasoning needs Claude."""
import sys, tempfile, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core.config as cfg
tmp = Path(tempfile.mkdtemp()); cfg.DB_PATH = tmp / "t.db"; cfg.EXPORT_DIR = tmp
import core.store as store; store.DB_PATH = cfg.DB_PATH; store.init()
from core import normalize, llm, analyst, award, exporter
analyst.EXPORT_DIR = tmp; exporter.EXPORT_DIR = tmp
from tests.fixture_ideal_extraction import build

rfx, ex = build(); store.put("rfx", rfx)
for k, v in ex.items(): store.put(f"extraction:{k}", v)
normalize.rebuild()

class B(types.SimpleNamespace):
    def model_dump(self, exclude_none=True): return {k: v for k, v in self.__dict__.items() if v is not None}
class Fake:
    def __init__(self, script): self.script = list(script); self.messages = self
    def create(self, **kw):
        blocks = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return types.SimpleNamespace(content=blocks, stop_reason=stop, usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))
tu = lambda i, n, inp: B(type="tool_use", id=i, name=n, input=inp)
llm.set_client(Fake([
  [tu("t1", "award_scenario", {"strategy": "line_wise_cheapest"}), tu("t2", "award_scenario", {"vendors": ["V1", "V3"]})],
  [tu("t3", "run_sql", {"sql": "SELECT vendor_id, SUM(annual_value_inr) v FROM quotes WHERE usable=1 GROUP BY vendor_id"}),
   tu("t4", "run_sql", {"sql": "DELETE FROM quotes"}),
   tu("t5", "make_chart", {"sql": "SELECT line_id, vendor_id, landed_inr_per_uom FROM quotes WHERE line_id IN ('L06','L07')", "chart_type": "grouped_bar", "x": "line_id", "y": "landed_inr_per_uom", "color": "vendor_id", "title": "t"}),
   tu("t6", "export_excel", {"filename": "award test", "sheets": [{"name": "quotes", "sql": "SELECT * FROM quotes"}]}),
   tu("t7", "get_evidence", {"vendor_id": "V4", "line_id": "L03"})],
  [B(type="text", text="Done.")]]))
res = analyst.ask([], "split among cleared")
for t in res["trace"]: print(t["tool"], t["ok"], t["output_preview"][:160])
assert res["answer"] == "Done." and not res["trace"][3]["ok"] and "read-only" in res["trace"][3]["output_preview"]
assert store.read_sql("SELECT COUNT(*) n FROM quotes").n[0] == 150
s1 = award.scenario(); s2 = award.scenario(vendors=["V1", "V3"])
print("strict eligible", s1["vendors_considered"], round(s1["total_inr"]), "| V1+V3", round(s2["total_inr"]), "saving", round(s1["total_inr"] - s2["total_inr"]))
print(s2["by_vendor"].to_string()); print(s2["notes"])
p = exporter.export_workbook(s2); print("xlsx", p.name, p.stat().st_size)
import openpyxl; print(openpyxl.load_workbook(p).sheetnames)
# override flow
ov = {"V4|L07": {"action": "set_price", "price_inr_per_uom": 43.5, "freight_inr_per_uom": 0, "reason": "called Suresh", "by": "buyer", "ts": "now"},
      "V1|L14": {"action": "confirm", "reason": "rate confirmed", "by": "buyer", "ts": "now"}}
store.put("overrides", ov); store.put("gate_overrides", {"V3": {"status": "CLEARED", "reason": "cert received"}}); normalize.rebuild()
print(store.read_sql("SELECT vendor_id,line_id,landed_inr_per_uom,review_status,confidence FROM quotes WHERE (vendor_id='V4' AND line_id='L07') OR (vendor_id='V1' AND line_id='L14')").to_string())
print(award.eligible_vendors())
print("ALL AGENT TESTS PASSED")
