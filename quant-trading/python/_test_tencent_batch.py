"""Test Tencent batch loading for PE/PB/turnover."""
import sys, os, urllib.request
sys.path.insert(0, ".")

def fetch_tencent_batch(codes):
    """Batch fetch PE/PB/turnover from Tencent finance API (supports ~50 per request)."""
    results = {}
    # Convert codes to Tencent format: sh600519, sz000858
    tencent_codes = []
    code_map = {}
    for c in codes:
        prefix = "sh" if c.startswith("6") else "sz"
        tc = f"{prefix}{c}"
        tencent_codes.append(tc)
        code_map[tc] = c
    
    # Batch request (max ~80 per call)
    batch_size = 50
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i:i + batch_size]
        query = ",".join(batch)
        url = f"http://qt.gtimg.cn/q={query}"
        try:
            resp = urllib.request.urlopen(url, timeout=10)
            data = resp.read().decode("gbk")
            for line in data.strip().split("\n"):
                line = line.strip()
                if not line or "~" not in line:
                    continue
                # Find the var name to get the tencent code
                eq_pos = line.find("=")
                if eq_pos < 0:
                    continue
                var_name = line[:eq_pos].strip().replace("v_", "")
                parts = line[eq_pos+1:].strip('" ;\r').split("~")
                if len(parts) < 47:
                    continue
                code = code_map.get(var_name, "")
                if not code:
                    # Try to extract from parts
                    code = parts[2] if len(parts) > 2 else ""
                
                try:
                    pe_dynamic = float(parts[39]) if parts[39] and parts[39] != "" else None
                    pb = float(parts[46]) if len(parts) > 46 and parts[46] and parts[46] != "" else None
                    turnover = float(parts[38]) if parts[38] and parts[38] != "" else None
                    price = float(parts[3]) if parts[3] and parts[3] != "" else None
                    name = parts[1] if len(parts) > 1 else ""
                    total_mv = float(parts[45]) if len(parts) > 45 and parts[45] and parts[45] != "" else None
                    
                    results[code] = {
                        "pe_ttm": pe_dynamic,
                        "pb": pb,
                        "turnover_rate": turnover,
                        "price": price,
                        "name": name,
                        "total_mv": round(total_mv / 10000, 2) if total_mv else None,  # 万→亿
                    }
                except (ValueError, IndexError):
                    pass
        except Exception as e:
            print(f"Batch {i} failed: {e}")
    
    return results

# Test
codes = ["600519", "000858", "601318", "000001", "600036",
         "002415", "000333", "601888", "300750", "603259"]

data = fetch_tencent_batch(codes)
print(f"Got data for {len(data)} / {len(codes)} stocks")
for c, d in data.items():
    print(f"  {c} ({d.get('name','')}): pe={d.get('pe_ttm')}, pb={d.get('pb')}, turnover={d.get('turnover_rate')}%, mv={d.get('total_mv')}亿")
