# AI 저장소 구조

현재 관리 대상은 다음과 같다.

```text
bareum-ai/
├── .github/                 # 협업 템플릿과 하네스 CI
├── docs/
│   ├── architecture/        # 저장소 구조와 책임 경계
│   ├── guide/               # AI 작업 안내
│   └── harness/             # 하네스 설계와 확장 기준
├── harness/
│   ├── src/project_harness/ # 저장소 정적 검사기와 CLI
│   ├── tests/               # 검사기 자체 테스트
│   └── reports/             # 실행 결과(Git 제외)
├── scripts/                 # 로컬·CI 검사 진입점
├── src/bareum_ai/           # AI 실행 코드
│   └── dataset/             # 데이터셋 구축 도구
│       └── parsing/         # 데이터셋 구축용 PDF 파서
├── tests/                   # AI 실행 코드 테스트 (pytest)
│   └── dataset/parsing/
├── .python-version          # 로컬 Python 버전 (3.14)
├── pyproject.toml           # 의존성·프로젝트 설정 (uv)
├── uv.lock                  # 의존성 잠금 파일
├── AGENTS.md                # 저장소 작업 지침
└── README.md                # 저장소 개요
```

AI 실행 코드는 `src/bareum_ai/` 아래에 모듈(처리 단계) 단위로 추가하고, 테스트는 루트 `tests/`에서 같은 구조를 따른다. 작업(이슈) 단위로 폴더를 만들지 않으며, 아직 구현하지 않는 모듈의 빈 디렉터리를 미리 만들지 않는다.

프롬프트, 규칙, 데이터와 평가 자산의 경로는 아직 이 문서에서 정하지 않는다. 실제 작업의 기술 선택과 소유 범위가 확정되면 구현·테스트·검사를 함께 추가한다.
