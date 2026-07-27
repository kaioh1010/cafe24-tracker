"""
상품 API 가격 필드 확인용 (임시).
- 상품 목록에서 price / retail_price / supply_price 등 가격 계열 필드 확인
- 주문에 실제 등장한 product_no 몇 개를 상품 API로 조회해 비교
GitHub Actions에서 1회 실행 후 삭제.
"""
import os, json, datetime
import tracker

def main():
    tok = tracker.refresh_access_token()
    access = tok["access_token"]
    if tok["refresh_token"] != tracker.REFRESH_TOKEN:
        tracker.update_github_secret("REFRESH_TOKEN", tok["refresh_token"])

    hdr = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}

    # 1) 상품 목록 몇 개 조회 -> 가격 필드 전체 구조
    import urllib.parse
    q = urllib.parse.urlencode({"limit": 3})
    status, res = tracker._req(f"{tracker.BASE}/api/v2/admin/products?{q}", headers=hdr)
    print("=== products 응답 status:", status, "===")
    products = res.get("products", [])
    if not products:
        print("상품 없음. 응답:", json.dumps(res, ensure_ascii=False)[:500])
        return

    p = products[0]
    print("\n=== 첫 상품 가격 계열 필드 ===")
    for k in sorted(p.keys()):
        kl = k.lower()
        if any(t in kl for t in ["price","cost","retail","supply","market"]):
            print(f"  {k} = {p[k]}")

    print("\n=== 첫 상품 전체 키 목록 ===")
    print(sorted(p.keys()))

    # 2) 특정 product_no 하나 상세 조회 (주문에 자주 나온 195 등)
    for pno in [195, 82, 11]:
        s2, r2 = tracker._req(f"{tracker.BASE}/api/v2/admin/products/{pno}", headers=hdr)
        if s2 == 200 and r2.get("product"):
            pr = r2["product"]
            print(f"\n=== product_no {pno}: {pr.get('product_name','')} ===")
            for k in ["price","retail_price","supply_price","product_price"]:
                print(f"  {k} = {pr.get(k)}")

if __name__ == "__main__":
    main()
