# 영역 구조와 의존성 경계

멀티레포의 제품 코드는 프론트엔드와 서버로 나눈다. GitHub 저장소는 `bareum-web`과 `bareum-server` 두 개이며, AI·공유 계약·하네스는 `bareum-server`에서 관리한다.

```text
공유 계약(확정 후) ──> bareum-server ── 공개 API ──> bareum-web
                          │
                          ├── evals
                          └── harness
```

제품 저장소는 `bareum-web`과 `bareum-server`로 구성한다. `bareum-server`는 백엔드와 AI를 관리하는 저장소이지만, 내부 모듈 경계와 실행 구조는 아직 확정하지 않았다. 공유 계약이 확정되면 `bareum-server/packages/contracts/`에 추가하고, 제공자 구현·`bareum-web` 소비·통합 테스트를 함께 검토한다.

하나의 서버 저장소에서 백엔드와 AI를 관리한다는 결정만 확정되어 있다. API와 AI의 코드 경로, 프로세스·패키지·이미지 분리 여부, 큐·통신 방식·물리 배포 환경은 결정 기록이 생긴 뒤 필요한 경로와 함께 추가한다.
