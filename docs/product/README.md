# 제품 문서 안내

제품 기획 원문과 세부 페이지 기획은 Notion에서 관리한다. 이 저장소는 원문을 복제하지 않고 서버 구현에 필요한 [`service-spec.md`](service-spec.md)만 요약 문서로 유지한다.

- 기획 변경은 원문에서 확정된 내용을 확인한 뒤 요약 문서와 관련 구현·검사에 반영한다.
- 미결 항목은 `service-spec.md`의 `결정 대기`에 남기며 구현에서 임의로 확정하지 않는다.
- 하네스는 문서의 필수 구성을 확인하며 내용의 정확성은 판정하지 않는다.
- 하네스 기준은 [`harness-v1.3.md`](../harness/harness-v1.3.md)를 따른다.

## 유지 규칙

1. 팀에서 확정된 변경만 이 요약 문서에 반영한다.
2. API·상태·오류·데이터 구조가 확정되면 `packages/contracts/`를 생성하고, 이후 변경에는 해당 계약을 함께 수정한다.
3. 백엔드·AI 책임이나 실행 조건의 변경은 `docs/architecture/repository-structure.md`, `docs/architecture/repository-boundaries.md`와 하네스 기준을 함께 검토한다. 구체 경로는 관련 아키텍처 결정 뒤 추가한다.
4. 미확정 내용은 `결정 대기` 또는 결정 기록으로 남기고 확정된 것처럼 작성하지 않는다.
