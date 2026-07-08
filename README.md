# 카페24 일간 판매량/매출 트래커

GitHub Actions로 매일 새벽 전날 주문을 수집해 상품별 수량/매출을 `data/daily_sales.csv`에 누적합니다. 서버 비용 0원.

## 준비 (최초 1회)

### 1. 카페24 앱 생성
1. [카페24 개발자센터](https://developers.cafe24.com) → 앱 생성
2. **Client ID / Client Secret** 확보
3. Scope: `mall.read_order`, `mall.read_product` 추가
4. Redirect URL 등록 (예: `https://localhost`)

### 2. 최초 토큰 발급
브라우저에서 아래 접속(값 치환) → 동의 → URL의 `code` 복사 (1분 내 사용):
```
https://{MALL_ID}.cafe24api.com/api/v2/oauth/authorize?response_type=code&client_id={CLIENT_ID}&state=x&redirect_uri={REDIRECT_URI}&scope=mall.read_order,mall.read_product
```
code로 토큰 발급:
```bash
CRED=$(printf '%s:%s' "$CLIENT_ID" "$CLIENT_SECRET" | base64)
curl -X POST "https://{MALL_ID}.cafe24api.com/api/v2/oauth/token" \
  -H "Authorization: Basic $CRED" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code={CODE}" \
  -d "redirect_uri={REDIRECT_URI}"
```
응답의 `refresh_token` 보관.

### 3. GitHub 레포 + Secrets
이 폴더를 GitHub 레포(프라이빗 권장)로 푸시 후 Settings → Secrets → Actions에 등록:

| Secret | 값 |
|---|---|
| `MALL_ID` | 쇼핑몰 ID |
| `CLIENT_ID` | 앱 Client ID |
| `CLIENT_SECRET` | 앱 Client Secret |
| `REFRESH_TOKEN` | 2단계에서 받은 refresh_token |
| `GH_PAT` | repo 권한 PAT (Secrets 자동 갱신용) |

`GH_PAT`: GitHub Settings → Developer settings → Fine-grained token → 해당 레포에 **Secrets: Read/Write**, **Contents: Read/Write** 권한 부여.

## 동작
- 매일 KST 04:00경 자동 실행 (Actions cron은 UTC·지연 가능)
- access_token은 매 실행 시 refresh_token으로 재발급
- refresh_token은 교환 시마다 새로 발급 → 자동으로 Secret 갱신 (2주 방치 시 만료되므로 데일리 실행이 곧 갱신)
- 수동/백필: Actions → Run workflow → 날짜 입력

## 주의
- 2주 이상 미실행 시 refresh_token 만료 → 2단계부터 재발급 필요
- `EXCLUDE_STATUS`(tracker.py)에서 취소/환불 상태코드 조정 가능
- 매출 계산식은 `상품가×수량 − 추가할인`. 쿠폰/적립 등 반영하려면 수정
