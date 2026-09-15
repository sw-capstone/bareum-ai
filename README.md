# bareum-server — 백엔드·AI 서비스 레포

`sw-capstone/bareum-server`는 바름 서비스의 백엔드와 AI 파이프라인을 함께 관리하는 저장소다. 공개 API, 세션·분석 작업·영속성, 문서 처리와 AI 판정 흐름을 담당하며, 프론트엔드는 별도 `bareum-web` 레포에서 공개 계약을 소비한다.

## 담당 범위

- `packages/contracts/`: 형식이 확정되면 추가할 서버·프론트엔드 공유 계약 경로
- 백엔드·AI 실행 코드: 아키텍처와 실행 경계가 확정된 뒤 추가
- `harness/`: 현재 저장소·문서 정적 검사를 실행하고, 계약·근거·AI 안전성·회귀 검사를 향후 연결할 검증 체계

서버와 AI 영역의 책임은 분리해 관리하되, 하나의 서버 레포에서 함께 개발한다. 영역 간 형식은 계약으로 연결하고, 근거가 검증되지 않은 결과는 확정 판정으로 노출하지 않는다.

## 저장소 구조

```text
.github/        이슈·PR 템플릿과 하네스 CI
docs/           기획·아키텍처·결정·개발·하네스 문서
harness/        하네스 정책·검사기·자체 테스트
scripts/        로컬과 CI의 공통 실행 진입점
```

`packages/contracts/`는 공유 계약이 확정되면 추가할 위치이며, 현재 구조에는 포함되지 않는다. 백엔드·AI 실행 코드와 기준 데이터·평가 자산의 경로도 관련 결정 후 정한다.

각 디렉터리의 세부 책임과 경계는 [`docs/architecture/repository-structure.md`](docs/architecture/repository-structure.md)와 [`docs/architecture/repository-boundaries.md`](docs/architecture/repository-boundaries.md)를 기준으로 한다. 모델, 프레임워크, 세부 수치와 팀 역할은 확정된 기획문서와 팀 결정에 따라 반영한다.

## 문서 안내

- 레포 구조: [`docs/architecture/repository-structure.md`](docs/architecture/repository-structure.md)
- 아키텍처 경계: [`docs/architecture/repository-boundaries.md`](docs/architecture/repository-boundaries.md)
- 개발 절차: [`docs/guide/development-guide.md`](docs/guide/development-guide.md)
- 제품 기획·서비스 명세: [`docs/product/README.md`](docs/product/README.md)
- GitHub 운영 규칙: [노션 협업 규칙 문서](https://app.notion.com/p/3d804af60ca8802480d3e7d65a29b5dd)

### 하네스 검증

하네스는 백엔드·AI 구현을 대신하는 기능이 아니다. 현재는 필수 경로·JSON·문서 링크·비밀정보 패턴·기획 문서 표식을 검사한다. Schema·ID 검사는 코드가 있지만 대상이 없어 `not_applicable`이며, 계약 호환성·근거·AI 안전성·성능 회귀는 관련 구현 후 연결한다. 적용 범위와 확장 기준은 [`docs/guide/development-guide.md`](docs/guide/development-guide.md), 구조 설계는 [`docs/harness/harness-v1.3.md`](docs/harness/harness-v1.3.md)에서 확인한다.

```bash
./scripts/run-harness.sh check
./scripts/test-harness.sh
```
