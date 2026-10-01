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
├── AGENTS.md                # 저장소 작업 지침
└── README.md                # 저장소 개요
```

제품 AI 코드, 프롬프트, 규칙, 데이터와 평가 자산의 경로는 아직 이 문서에서 정하지 않는다. 실제 작업의 기술 선택과 소유 범위가 확정되면 구현·테스트·검사를 함께 추가한다.
