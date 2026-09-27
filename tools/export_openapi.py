"""Write contracts/openapi.json (the API contract) for client code generation, e.g. TypeScript types:
npx openapi-typescript contracts/openapi.json -o src/uigen-api.d.ts"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from service.api import create_app  # noqa: E402

spec = create_app(lambda: None, ["export-only"], runs_dir=ROOT / "runs").openapi()
out = ROOT / "contracts" / "openapi.json"
out.write_text(json.dumps(spec, indent=2) + "\n")
print("wrote", out.relative_to(ROOT), "-", len(spec["paths"]), "paths")
