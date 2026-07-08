"""
주문 API 응답 필드 확인용 (임시). GitHub Actions에서 1회 실행 후 삭제.
민감정보(주문자/연락처/주소/이메일 등)는 자동 마스킹.
네이버페이/마켓 주문을 우선적으로 찾아 출력.
"""
import os, json, datetime
import tracker

SENSITIVE = {"member_id","member_email","buyer_name","buyer_email","buyer_phone",
             "buyer_cellphone","receiver_name","receiver_phone","receiver_cellphone",
             "receiver_address","receiver_address_full","receiver_zipcode","order_id",
             "billing_name","member_authentication","receiver_city","receiver_state",
             "receiver_address1","receiver_address2","address","name","phone","cellphone",
             "email","ip","user_id","bank_account_no","bank_account_owner_name",
             "market_seller_id","market_order_no"}

def mask(o):
    if isinstance(o, dict):
        return {k: ("***" if k in SENSITIVE else mask(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask(x) for x in o]
    return o

def money_fields(o):
    out = {}
    for k in sorted(o.keys()):
        if k == "items": continue
        kl = k.lower()
        if any(t in kl for t in ["price","amount","payment","discount","point","coupon",
                                  "mileage","total","paid","actual","supply","market","naver","order_place"]):
            out[k] = o[k]
    return out

def dump_order(o, tag):
    print(f"\n========== {tag} ==========")
    print(">> 주문 금액/마켓 관련 필드:")
    print(json.dumps(mask(money_fields(o)), ensure_ascii=False, indent=2))
    items = o.get("items", [])
    print(f">> items {len(items)}개, 첫 item 금액 필드:")
    if items:
        it = items[0]
        keys = ["product_no","product_name","product_price","option_price",
                "additional_discount_price","coupon_discount_price","app_item_discount_amount",
                "payment_amount","quantity","claim_quantity","order_status","status_code",
                "status_text","supply_price","market_item_no","market_discount_amount"]
        print(json.dumps({k: it.get(k) for k in keys}, ensure_ascii=False, indent=2))

def main():
    tok = tracker.refresh_access_token()
    access = tok["access_token"]
    if tok["refresh_token"] != tracker.REFRESH_TOKEN:
        tracker.update_github_secret("REFRESH_TOKEN", tok["refresh_token"])

    day = os.environ.get("TARGET_DATE") or (datetime.datetime.now(tracker.KST) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    orders = tracker.fetch_orders(access, day)
    print(f"=== {day} 주문 {len(orders)}건 ===")
    if not orders:
        print("주문 없음. 다른 날짜 지정.")
        return

    # 마켓/네이버페이 주문 찾기
    naver = [o for o in orders if o.get("market_id") or o.get("naverpay_payment_information") or o.get("market_order_no")]
    print(f"마켓/네이버페이 의심 주문: {len(naver)}건")

    # 결제수단 분포
    pm = {}
    for o in orders:
        key = str(o.get("payment_method"))
        pm[key] = pm.get(key, 0) + 1
    print("payment_method 분포:", pm)

    # 상태 분포
    dist = {}
    for o in orders:
        for it in o.get("items", []):
            s = it.get("order_status"); dist[s] = dist.get(s,0)+1
    print("order_status 분포:", dist)

    if naver:
        dump_order(naver[0], "네이버페이/마켓 주문 샘플")
    else:
        print("\n네이버페이 주문을 못 찾음. market_id가 있는 주문이 없습니다.")
        # 참고로 첫 주문 출력
        dump_order(orders[0], "일반 주문 샘플 (참고)")

if __name__ == "__main__":
    main()
