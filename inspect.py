"""
신규/재구매 식별자 확인용 (임시). 개인정보 원본은 출력하지 않음.
GitHub Actions에서 1회 실행 후 삭제.
"""
import os, json, hashlib, datetime
import tracker

def h(v):
    if v is None or v == "":
        return None
    return hashlib.sha256(str(v).encode()).hexdigest()[:8]

def main():
    tok = tracker.refresh_access_token()
    access = tok["access_token"]
    if tok["refresh_token"] != tracker.REFRESH_TOKEN:
        tracker.update_github_secret("REFRESH_TOKEN", tok["refresh_token"])

    day = os.environ.get("TARGET_DATE") or datetime.datetime.now(tracker.KST).strftime("%Y-%m-%d")
    orders = tracker.fetch_orders(access, day)
    print(f"=== {day} 주문 {len(orders)}건 ===\n")
    if not orders:
        print("주문 없음. 다른 날짜 지정.")
        return

    o = orders[0]
    id_candidates = ["first_order","member_id","member_email","member_authentication",
                     "buyer_name","buyer_email","order_id","order_place_id","group_no_when_ordering"]
    print("=== 주문 레벨 식별 후보 필드 존재/샘플 ===")
    for k in id_candidates:
        present = k in o
        val = o.get(k)
        if k in ("first_order","member_authentication","order_place_id"):
            shown = val
        else:
            shown = h(val)
        print(f"  {k}: present={present}, sample={shown}")

    fo = {}
    for od in orders:
        v = od.get("first_order")
        fo[str(v)] = fo.get(str(v), 0) + 1
    print(f"\n=== first_order 값 분포 ===\n{fo}")

    member_cnt = sum(1 for od in orders if od.get("member_id"))
    print(f"\n=== 회원 주문 {member_cnt} / 비회원 {len(orders)-member_cnt} ===")

    ma = {}
    for od in orders:
        v = od.get("member_authentication")
        ma[str(v)] = ma.get(str(v), 0) + 1
    print(f"\n=== member_authentication 분포 ===\n{ma}")

    from collections import Counter
    hashes = [h(od.get("member_id")) for od in orders if od.get("member_id")]
    c = Counter(hashes)
    repeat = {k:v for k,v in c.items() if v>1}
    print(f"\n=== 이 날짜 내 동일 회원 복수주문(해시:건수) ===\n{repeat if repeat else '없음'}")

if __name__ == "__main__":
    main()
