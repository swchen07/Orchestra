"""Export public action metadata; never expose the backend directory to Vite."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'backend/open_webui/execution/node_actions.json'
target = root / 'src/lib/components/execution/nodeActions.generated.json'
target.write_text(json.dumps(json.loads(source.read_text()), ensure_ascii=False, indent=2) + '\n')
print('Exported public node action metadata')
