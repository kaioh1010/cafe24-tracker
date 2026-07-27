"""
카페24 상품별 일간 판매량/매출 트래커
- 주문을 Admin API로 수집
- 상품(product_no)별 순매출/판매량 집계 -> data/daily_sales.csv
- 날짜별 신규/재구매/비회원 요약 -> data/daily_summary.csv
- 신규(첫구매) 주문의 상품별 집계 -> data/daily_first_products.csv
- access_token 만료 시 refresh_token 자동 재발급, 새 refresh_token은 GitHub Secret에 저장
환경변수: MALL_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN
GitHub Actions 전용: GH_PAT, GH_REPO
"""
import os
import csv
import json
import base64
import datetime
import urllib.request
import urllib.parse
import urllib.error

MALL_ID = os.environ["MALL_ID"]
CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["REFRESH_TOKEN"]

BASE = f"https://{MALL_ID}.cafe24api.com"
KST = datetime.timezone(datetime.timedelta(hours=9))
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CSV_PATH = os.path.join(DATA_DIR, "daily_sales.csv")
SUMMARY_PATH = os.path.join(DATA_DIR, "daily_summary.csv")
FIRST_PATH = os.path.join(DATA_DIR, "daily_first_products.csv")

# 취소(C)/교환(E)/반품(R) 상태 품목은 순매출에서 제외
CANCEL_PREFIXES = ("C", "E", "R")

# 실결제(순매출)에서 차감할 주문 단위 할인 필드 (배송비 관련 제외)
ORDER_DISCOUNT_FIELDS = (
    "coupon_discount_price",
    "points_spent_amount",
    "credits_spent_amount",
    "membership_discount_amount",
    "set_product_discount_amount",
    "app_discount_amount",
    "market_other_discount_amount",
)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _item_base(it):
    """품목 상품 기준액: option_price가 있으면 그것, 없으면 product_price*qty."""
    op = _f(it.get("option_price"))
    if op > 0:
        return op
    return _f(it.get("product_price")) * int(it.get("quantity", 0) or 0)


def _req(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def refresh_access_token():
    cred = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }).encode()
    status, res = _req(
        f"{BASE}/api/v2/oauth/token",
        method="POST",
        headers={
            "Authorization": f"Basic {cred}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=body,
    )
    if status != 200:
        raise RuntimeError(f"토큰 재발급 실패 {status}: {res}")
    return res


def update_github_secret(name, value):
    pat = os.environ.get("GH_PAT")
    repo = os.environ.get("GH_REPO")
    if not pat or not repo:
        print("[warn] GH_PAT/GH_REPO 없음 - Secret 자동 갱신 생략")
        return
    try:
        from nacl import encoding, public
    except ImportError:
        os.system("pip install pynacl -q")
        from nacl import encoding, public

    api = f"https://api.github.com/repos/{repo}/actions/secrets"
    hdr = {"Authorization": f"Bearer {pat}",
           "Accept": "application/vnd.github+json",
           "User-Agent": "cafe24-tracker"}
    status, key = _req(f"{api}/public-key", headers=hdr)
    if status != 200:
        print(f"[warn] public-key 조회 실패: {key}")
        return
    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    enc = base64.b64encode(public.SealedBox(pk).encrypt(value.encode())).decode()
    payload = json.dumps({"encrypted_value": enc, "key_id": key["key_id"]}).encode()
    status, res = _req(f"{api}/{name}", method="PUT", headers=hdr, data=payload)
    if status in (201, 204):
        print(f"[ok] Secret {name} 갱신됨")
    else:
        print(f"[warn] Secret 갱신 실패 {status}: {res}")


def fetch_orders(access_token, day):
    hdr = {"Authorization": f"Bearer {access_token}",
           "Content-Type": "application/json"}
    orders, offset, limit = [], 0, 100
    while True:
        q = urllib.parse.urlencode({
            "start_date": f"{day}T00:00:00+09:00",
            "end_date": f"{day}T23:59:59+09:00",
            "date_type": "order_date",
            "embed": "items",
            "limit": limit,
            "offset": offset,
        })
        status, res = _req(f"{BASE}/api/v2/admin/orders?{q}", headers=hdr)
        if status != 200:
            raise RuntimeError(f"주문 조회 실패 {status}: {res}")
        batch = res.get("orders", [])
        orders.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return orders


def _order_customer_type(o):
    """신규/재구매/비회원 분류.
    - 비회원(member_authentication != 'T' 또는 member_id 없음): 'guest'
    - 회원 & first_order == 'T': 'new'
    - 회원 & 그 외: 'repeat'
    """
    is_member = o.get("member_authentication") == "T" and bool(o.get("member_id"))
    if not is_member:
        return "guest"
    return "new" if o.get("first_order") == "T" else "repeat"


def _order_revenue_and_base(o):
    """주문의 순매출, 정상가합(할인 전 상품기준액)을 반환.
    aggregate와 동일한 규칙(취소 제외, 부분취소 반영, 할인 배분)."""
    items = o.get("items", [])
    normal = [it for it in items
              if not str(it.get("order_status", "")).startswith(CANCEL_PREFIXES)]
    base_sum = sum(_item_base(it) for it in normal)
    if base_sum <= 0:
        return 0.0, 0.0, []

    aoa = o.get("actual_order_amount") or {}
    order_discount = sum(_f(aoa.get(k)) for k in ORDER_DISCOUNT_FIELDS)

    revenue_total = 0.0
    base_total = 0.0
    item_rev = []  # (product_no, product_name, qty, revenue)
    for it in normal:
        qty_ordered = int(it.get("quantity", 0) or 0)
        claim = int(it.get("claim_quantity", 0) or 0)
        qty = qty_ordered - claim
        if qty <= 0:
            continue
        base = _item_base(it)
        if qty_ordered > 0 and qty != qty_ordered:
            base = base * qty / qty_ordered
        item_disc = _f(it.get("additional_discount_price")) + _f(it.get("coupon_discount_price"))
        if qty_ordered > 0 and qty != qty_ordered:
            item_disc = item_disc * qty / qty_ordered
        share = (base / base_sum) if base_sum else 0
        alloc = order_discount * share
        rev = base - item_disc - alloc
        if rev < 0:
            rev = 0.0
        revenue_total += rev
        base_total += base
        item_rev.append((it.get("product_no"), it.get("product_name", ""), qty, rev))
    return revenue_total, base_total, item_rev


def aggregate(orders):
    """상품별 순매출/판매량 집계 (daily_sales.csv용)."""
    agg = {}
    for o in orders:
        _, _, item_rev = _order_revenue_and_base(o)
        for pno, name, qty, rev in item_rev:
            if pno not in agg:
                agg[pno] = {"product_name": name, "qty": 0, "revenue": 0.0}
            agg[pno]["qty"] += qty
            agg[pno]["revenue"] += rev
    return agg


def aggregate_customer(orders):
    """신규/재구매/비회원 요약 + 신규 첫구매 상품 집계.
    반환: (summary dict, first_products dict)
    summary: 구분별 {orders, revenue}, 그리고 전체 base_sum(정상가합)/revenue_sum
    """
    summary = {
        "new":    {"orders": 0, "revenue": 0.0},
        "repeat": {"orders": 0, "revenue": 0.0},
        "guest":  {"orders": 0, "revenue": 0.0},
        "base_sum": 0.0,      # 전체 정상가합
        "revenue_sum": 0.0,   # 전체 순매출합
        "order_count": 0,     # 매출 있는 주문 수
    }
    first_products = {}  # pno -> {name, qty, revenue}  (신규 주문만)

    for o in orders:
        rev, base, item_rev = _order_revenue_and_base(o)
        if rev <= 0 and base <= 0:
            continue
        ctype = _order_customer_type(o)
        summary[ctype]["orders"] += 1
        summary[ctype]["revenue"] += rev
        summary["base_sum"] += base
        summary["revenue_sum"] += rev
        summary["order_count"] += 1

        if ctype == "new":
            for pno, name, qty, r in item_rev:
                if pno not in first_products:
                    first_products[pno] = {"product_name": name, "qty": 0, "revenue": 0.0}
                first_products[pno]["qty"] += qty
                first_products[pno]["revenue"] += r

    return summary, first_products


def _rewrite_dated(path, header, day, new_rows):
    """path의 CSV에서 day 행 제거 후 new_rows 추가 (멱등)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    rows = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f)]
    body = [r for r in rows[1:]] if rows else []
    body = [r for r in body if r and r[0] != day]
    body.extend(new_rows)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)


def save_csv(day, agg):
    _rewrite_dated(
        CSV_PATH,
        ["date", "product_no", "product_name", "quantity", "revenue"],
        day,
        [[day, pno, v["product_name"], v["qty"], round(v["revenue"])]
         for pno, v in sorted(agg.items())],
    )


def save_summary(day, summary):
    row = [
        day,
        summary["new"]["orders"], round(summary["new"]["revenue"]),
        summary["repeat"]["orders"], round(summary["repeat"]["revenue"]),
        summary["guest"]["orders"], round(summary["guest"]["revenue"]),
        summary["order_count"], round(summary["base_sum"]), round(summary["revenue_sum"]),
    ]
    _rewrite_dated(
        SUMMARY_PATH,
        ["date", "new_orders", "new_revenue", "repeat_orders", "repeat_revenue",
         "guest_orders", "guest_revenue", "order_count", "base_sum", "revenue_sum"],
        day,
        [row],
    )


def save_first_products(day, first_products):
    _rewrite_dated(
        FIRST_PATH,
        ["date", "product_no", "product_name", "quantity", "revenue"],
        day,
        [[day, pno, v["product_name"], v["qty"], round(v["revenue"])]
         for pno, v in sorted(first_products.items())],
    )


def main():
    target = os.environ.get("TARGET_DATE")
    if not target:
        target = datetime.datetime.now(KST).strftime("%Y-%m-%d")

    tok = refresh_access_token()
    access = tok["access_token"]
    new_refresh = tok["refresh_token"]
    if new_refresh != REFRESH_TOKEN:
        update_github_secret("REFRESH_TOKEN", new_refresh)

    orders = fetch_orders(access, target)

    agg = aggregate(orders)
    save_csv(target, agg)

    summary, first_products = aggregate_customer(orders)
    save_summary(target, summary)
    save_first_products(target, first_products)

    total_q = sum(v["qty"] for v in agg.values())
    total_r = sum(v["revenue"] for v in agg.values())
    s = summary
    print(f"[{target}] 상품 {len(agg)}종 / 수량 {total_q} / 매출 {round(total_r):,}원 저장 완료")
    print(f"  신규 {s['new']['orders']}건({round(s['new']['revenue']):,}) / "
          f"재구매 {s['repeat']['orders']}건({round(s['repeat']['revenue']):,}) / "
          f"비회원 {s['guest']['orders']}건({round(s['guest']['revenue']):,})")
    if s["base_sum"] > 0:
        print(f"  순매출/정상가 = {round(s['revenue_sum']/s['base_sum']*100,1)}% "
              f"/ 평균구매액 {round(s['revenue_sum']/max(s['order_count'],1)):,}원")


if __name__ == "__main__":
    main()
