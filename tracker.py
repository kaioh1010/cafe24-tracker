"""
카페24 상품별 일간 판매량/매출 트래커
- 전날 주문을 Admin API로 수집
- 상품(product_no)별 수량/매출 집계
- data/daily_sales.csv 에 누적 저장
- access_token 만료 시 refresh_token 으로 자동 재발급
- 새 refresh_token 은 GitHub Secrets(REFRESH_TOKEN)에 자동 업데이트
환경변수: MALL_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN
GitHub Actions 전용: GH_PAT, GH_REPO (Secrets 자동 갱신용)
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

# 순매출 집계: 취소(C)/교환(E)/반품(R) 상태의 품목은 제외.
# 정상 품목도 부분취소 수량(claim_quantity)을 빼고 실판매 수량으로 계산.
CANCEL_PREFIXES = ("C", "E", "R")

# 실결제(순매출)에서 차감할 주문 단위 할인 필드 (배송비 관련 제외)
ORDER_DISCOUNT_FIELDS = (
    "coupon_discount_price",        # 주문 쿠폰
    "points_spent_amount",          # 적립금 사용
    "credits_spent_amount",         # 예치금 사용
    "membership_discount_amount",   # 회원 할인
    "set_product_discount_amount",  # 세트상품 할인
    "app_discount_amount",          # 앱 할인
    "market_other_discount_amount", # 마켓 기타 할인
)


def _f(v):
    """문자열/None 금액을 float로."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _item_base(it):
    """품목의 상품 기준액: option_price가 있으면 그것, 없으면 product_price*qty."""
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
    """refresh_token으로 access_token 재발급. 새 토큰 dict 반환."""
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
    """GitHub Actions 환경에서 새 refresh_token을 Secrets에 저장."""
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
    """day(YYYY-MM-DD) 하루치 주문 전체 수집(페이지네이션)."""
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


def aggregate(orders):
    """상품별 순매출/판매량 집계.
    - 취소/교환/반품 상태 품목 제외
    - 부분취소 수량 차감(quantity - claim_quantity)
    - 실결제 기준: 품목 자체 할인 차감 후, 주문 단위 할인을 상품기준액 비율로 배분
    - 적립금 사용 등도 차감(실결제금액 기준)
    """
    agg = {}
    for o in orders:
        items = o.get("items", [])

        # 이 주문에서 정상(비취소) 품목만 대상으로 상품기준액 비율 계산
        normal = [it for it in items
                  if not str(it.get("order_status", "")).startswith(CANCEL_PREFIXES)]
        base_sum = sum(_item_base(it) for it in normal)
        if base_sum <= 0:
            continue

        # 주문 단위 할인 총액(배분 대상)
        aoa = o.get("actual_order_amount") or {}
        order_discount = sum(_f(aoa.get(k)) for k in ORDER_DISCOUNT_FIELDS)

        for it in normal:
            qty_ordered = int(it.get("quantity", 0) or 0)
            claim = int(it.get("claim_quantity", 0) or 0)
            qty = qty_ordered - claim
            if qty <= 0:
                continue

            base = _item_base(it)
            # 부분취소 시 상품기준액도 실판매 비율로 축소
            if qty_ordered > 0 and qty != qty_ordered:
                base = base * qty / qty_ordered

            # 품목 자체 할인
            item_disc = _f(it.get("additional_discount_price")) + _f(it.get("coupon_discount_price"))
            if qty_ordered > 0 and qty != qty_ordered:
                item_disc = item_disc * qty / qty_ordered

            # 주문 단위 할인을 상품기준액 비율로 배분
            share = (base / base_sum) if base_sum else 0
            alloc = order_discount * share

            revenue = base - item_disc - alloc
            if revenue < 0:
                revenue = 0.0

            pno = it.get("product_no")
            name = it.get("product_name", "")
            if pno not in agg:
                agg[pno] = {"product_name": name, "qty": 0, "revenue": 0.0}
            agg[pno]["qty"] += qty
            agg[pno]["revenue"] += revenue
    return agg


def save_csv(day, agg):
    os.makedirs(DATA_DIR, exist_ok=True)
    new = not os.path.exists(CSV_PATH)
    # 같은 날짜 기존 행 제거 후 재기록(재실행 멱등성)
    rows = []
    if not new:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.reader(f)]
    header = ["date", "product_no", "product_name", "quantity", "revenue"]
    body = [r for r in rows[1:]] if rows else []
    body = [r for r in body if r and r[0] != day]
    for pno, v in sorted(agg.items()):
        body.append([day, pno, v["product_name"], v["qty"], round(v["revenue"])])
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)


def main():
    # TARGET_DATE 없으면 KST 오늘을 수집(실행 시각이 밀려도 날짜가 안 꼬임).
    # 전날 확정치가 필요한 경우 워크플로에서 TARGET_DATE로 어제를 명시해 넘김.
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
    total_q = sum(v["qty"] for v in agg.values())
    total_r = sum(v["revenue"] for v in agg.values())
    print(f"[{target}] 상품 {len(agg)}종 / 수량 {total_q} / 매출 {round(total_r):,}원 저장 완료")


if __name__ == "__main__":
    main()
