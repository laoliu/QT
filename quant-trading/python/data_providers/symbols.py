"""Instrument codes whose stock and index namespaces overlap."""

INDEX_CODES = frozenset({
    "000001", "000002", "000003", "000016", "000300", "000688", "000852",
    "000903", "000905", "000906", "000985",
    "399001", "399005", "399006", "399106", "399300", "399673",
})


def to_qmt_code(symbol: str, market: str = "cn_stock") -> str:
    code = symbol.strip().upper()
    if code.endswith((".SH", ".SZ", ".BJ")):
        return code
    if market == "index":
        return f"{code}.SZ" if code.startswith("399") else f"{code}.SH"
    if market == "convertible_bond":
        return f"{code}.SH" if code.startswith("11") else f"{code}.SZ"
    if market == "cn_stock":
        exchange = "SH" if code.startswith(("6", "5", "9")) else "SZ"
        if code.startswith(("4", "8", "92")):
            exchange = "BJ"
        return f"{code}.{exchange}"
    return symbol
