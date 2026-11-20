"""兼容入口，见 ``appliance_price_bridge.__main__``。"""

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from appliance_price_bridge.__main__ import main

    raise SystemExit(main())
