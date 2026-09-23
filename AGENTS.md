# bareum-server-ai 작업 지침

- AI 처리 코드와 관련 테스트·평가를 이 저장소에서 관리한다.
- 현재 하네스는 이 저장소 내부만 검사하며 서버나 웹 검사 결과를 대신하지 않는다.
- 제품 기능·모델·평가 기준은 확정된 기획과 결정에 따라 구현한다.
- 공개 API는 bareum-server, 화면은 bareum-web에서 관리한다.
- 서버↔AI 형식 변경은 담당자 합의 후 서버 계약과 영향받는 구현·테스트를 함께 검토한다.
- 검사 코드는 영역 담당자가 작성하고 하네스 담당자를 필수 리뷰어로 지정한다.
- 검사 명령은 `./scripts/run-harness.sh check`, `./scripts/test-harness.sh`다.
- 공통 개발 절차는 `docs/guide/development-guide.md`, 하네스 기준은 `docs/harness/harness-v1.4.md`와 `docs/harness/harness-file-roadmap.md`를 따른다.
