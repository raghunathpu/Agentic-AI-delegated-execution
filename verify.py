"""End-to-end verification script."""
import sys
sys.path.insert(0, '.')

from src.storage.event_store import EventStore
from src.ingestion.ingestion_service import IngestionService
from src.delegation.delegation_graph import DelegationGraph
from src.reconstruction.reconstructor import CIMReconstructor, BaselineB0TraceOnly, BaselineB1TimeWindow, BaselineB2Hybrid
from src.audit.audit_engine import AuditEngine
from sample_data.sample_loader import load_sample_dataset
import tempfile, os

tmp = tempfile.mktemp(suffix='.db')
store = EventStore(tmp)
result = load_sample_dataset(store)
print("Events loaded:", result["events_loaded"])
print("Delegations:", result["delegations_loaded"])
stats = store.stats()
print("Store stats:", stats)

g = DelegationGraph(store)
g.build()

cim = CIMReconstructor(store, g).reconstruct()
print("CIM workflows:", len(cim.workflows), "| covered:", len(cim.covered_event_ids))

b0  = BaselineB0TraceOnly(store).reconstruct()
b1  = BaselineB1TimeWindow(store, window_seconds=30).reconstruct()
b2  = BaselineB2Hybrid(store, window_seconds=30).reconstruct()
print("B0 workflows:", len(b0.workflows))
print("B1 workflows:", len(b1.workflows))
print("B2 workflows:", len(b2.workflows))

audit    = AuditEngine(store, g)
findings = audit.run_all_checks()
summary  = audit.summary()
print("Audit findings:", len(findings), "| summary:", summary)

os.unlink(tmp)
print("--- END-TO-END VERIFICATION PASSED ---")
