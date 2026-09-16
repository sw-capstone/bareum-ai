# DEC-REPO-001 저장소 구성 방식

- 상태: 결정
- 소유 역할: `project-team`
- 결정: 멀티레포를 사용하되 제품 코드는 프론트엔드와 서버로만 분리한다.
- 저장소 구성: `sw-capstone/bareum-web`, `sw-capstone/bareum-server`
- AI 위치: `bareum-server`에서 관리한다. 구현 모듈과 실행 단위는 추후 확정하며, AI를 별도 저장소로 분리하지 않는다.
- 계약·하네스: 공통 계약은 형식이 확정되면 `bareum-server/packages/contracts`에 추가하고, 하네스 구현은 `bareum-server/harness`에서 관리한다. 평가 자산의 위치는 평가 범위와 형식이 확정된 뒤 결정한다.
- 이유: 프론트엔드와 백엔드의 배포·권한은 분리하면서, 서버 오케스트레이션과 AI 파이프라인 사이의 불필요한 저장소 경계를 줄인다.
- 영향: 서버와 AI의 책임·테스트 경계는 유지한다. 저장소가 하나라는 이유로 내부 결합을 허용하지 않는다.
- 전환 순서: bareum-server 기준선·계약 확정 → bareum-web 연동 → bareum-server 통합 Smoke
- 재검토 조건: backend 배포 주기, 권한, AI 독립 배포 필요성 또는 저장소 내부 결합 비용이 병목으로 측정될 때 재검토한다.
