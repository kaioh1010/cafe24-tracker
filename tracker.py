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

# 매출에서 제외할 주문상태(취소/환불/교환). 필요에 맞게 조정.
EXCLUDE_STATUS = {"C40", "C41", "C42", "C43", "C44", "C45", "C46", "C47", "C48", "C49"}


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
    """product_no별 수량/매출 집계."""
    agg = {}
    for o in orders:
        for it in o.get("items", []):
            if it.get("order_status") in EXCLUDE_STATUS:
                continue
            pno = it.get("product_no")
            name = it.get("product_name", "")
            qty = int(it.get("quantity", 0) or 0)
            price = float(it.get("product_price", 0) or 0)
            disc = float(it.get("additional_discount_price", 0) or 0)
            revenue = (price * qty) - disc
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
    target = os.environ.get("TARGET_DATE")
    if not target:
        target = (datetime.datetime.now(KST) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

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
