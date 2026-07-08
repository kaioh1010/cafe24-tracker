"""
주문 API 응답 필드 확인용 (임시). GitHub Actions에서 1회 실행 후 삭제.
민감정보(주문자/연락처/주소/이메일 등)는 자동 마스킹.
"""
import os, json, datetime
import tracker

# 민감 필드는 값 대신 "***"로 치환
SENSITIVE = {"member_id","member_email","buyer_name","buyer_email","buyer_phone",
             "buyer_cellphone","receiver_name","receiver_phone","receiver_cellphone",
             "receiver_address","receiver_address_full","receiver_zipcode","order_id",
             "billing_name","member_authentication","receiver_city","receiver_state",
             "receiver_address1","receiver_address2","address","name","phone","cellphone",
             "email","ip","user_id","order_from_mobile","nation_code"}

def mask(o):
    if isinstance(o, dict):
        return {k: ("***" if k in SENSITIVE else mask(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask(x) for x in o]
    return o

def main():
    tok = tracker.refresh_access_token()
    access = tok["access_token"]
    new_refresh = tok["refresh_token"]
    if new_refresh != tracker.REFRESH_TOKEN:
        tracker.update_github_secret("REFRESH_TOKEN", new_refresh)

    day = os.environ.get("TARGET_DATE") or (datetime.datetime.now(tracker.KST) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    orders = tracker.fetch_orders(access, day)
    print(f"=== {day} 주문 {len(orders)}건 ===")
    if not orders:
        print("주문이 없습니다. TARGET_DATE로 데이터 있는 날짜를 지정하세요.")
        return

    o = orders[0]
    print("\n=== 주문 레벨 필드 (키 목록) ===")
    print(sorted([k for k in o.keys() if k != "items"]))

    print("\n=== 주문 레벨 금액 관련 필드 ===")
    for k in sorted(o.keys()):
        if k == "items": continue
        kl = k.lower()
        if any(t in kl for t in ["price","amount","payment","discount","point","coupon","mileage","total","paid","actual","supply"]):
            print(f"  {k} = {o[k]}")

    items = o.get("items", [])
    print(f"\n=== items {len(items)}개, 첫 item 전체 (마스킹) ===")
    if items:
        print(json.dumps(mask(items[0]), ensure_ascii=False, indent=2))

    print("\n=== 주문 상태 값 분포 (order_status) ===")
    dist = {}
    for od in orders:
        for it in od.get("items", []):
            s = it.get("order_status")
            dist[s] = dist.get(s, 0) + 1
    print(dist)

if __name__ == "__main__":
    main()
