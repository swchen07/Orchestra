"""Verify the vendored source contract without network access or upstream writes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from open_webui.integrations.cc_switch.adapter import contract

if __name__ == '__main__':
    value = contract()
    print(
        f'CC Switch {value["upstream"]["version"]} @ {value["upstream"]["commit"]} · schema {value["upstream"]["schema_version"]} · {len(value["vendor_blobs"])} source blobs verified'
    )
