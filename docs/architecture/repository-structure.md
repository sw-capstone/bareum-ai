# 저장소 구조와 실행 경계

## 현재 저장소 결정

제품 코드는 멀티레포로 운영한다.

| 저장소 | 책임 |
| --- | --- |
| `sw-capstone/bareum-web` | 화면과 공개 API 계약 소비 |
| `sw-capstone/bareum-server` | 백엔드 API, AI 파이프라인, 공유 계약, 평가와 하네스 |

백엔드와 AI를 별도 Git 저장소로 나누지 않는다. 다만 같은 저장소에 있다고 해서 같은 프로세스나 컨테이너에서 실행해야 하는 것은 아니다.

## 현재 서버 저장소 구조

```text
bareum-server/
├── .github/                 # 이슈·PR 템플릿과 하네스 CI
├── docs/
│   └── architecture/
│       ├── repository-boundaries.md
│       └── repository-structure.md
├── harness/
├── scripts/
├── src/bareum_ai/           # AI 처리 코드 (Python 3.14, FastAPI)
├── pyproject.toml           # 의존성·빌드 설정 (uv로 관리)
└── uv.lock
```

AI 처리 코드는 `src/bareum_ai/` 아래에 모듈(처리 단계) 단위로 추가하고, 테스트는 `tests/`에서 같은 구조를 따른다. 작업(이슈) 단위로 폴더를 만들지 않으며, 아직 구현하지 않는 모듈의 빈 디렉터리를 미리 만들지 않는다.

백엔드와 AI는 `bareum-server`에서 관리한다는 저장소 경계만 확정되어 있다. 백엔드·AI 실행 코드의 경로와 실행·배포 단위는 아직 정하지 않았으므로 현재 구조에 해당 디렉터리나 하위 아키텍처 문서를 미리 만들지 않는다. API와 AI를 하나의 서버 저장소에서 관리한다는 결정만으로 코드 경로나 프로세스 운영이 확정되는 것은 아니다.

## 현재 구조의 적용 범위

이 문서는 확정된 저장소 분리와 현재 관리 대상 디렉터리만 정의한다. 백엔드·AI 실행 코드, 공유 계약, 인프라·배포 설정은 각 결정이 완료된 뒤 해당 구현과 함께 추가한다. 하네스는 결정되지 않은 경로를 검사 기준으로 사용하지 않는다.

## 관련 문서

- [`repository-boundaries.md`](repository-boundaries.md)
- [`DEC-REPO-001`](../decisions/DEC-REPO-001-repository-strategy.md)
