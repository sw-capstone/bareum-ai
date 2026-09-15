# 기획 문서 관리

## 기획 문서 안내

[`planning-final-v1.1.md`](planning-final-v1.1.md)는 현재 저장소에서 관리하는 기획 문서다.

- 문서 버전: `v1.1`
- 관리 경로: `docs/product/planning-final-v1.1.md`
- 변경 순서: 기획 변경 제안 → 이슈와 결정 상태 확인 → 기준 문서 수정 → 계약·테스트·하네스 검사 → PR 리뷰
- 미결 항목: `service-spec.md`의 `결정 대기`에 남기며 구현에서 임의로 확정하지 않는다.
- 하네스 기준: [`docs/harness/harness-v1.3.md`](../harness/harness-v1.3.md)
- 구현용 요약: [`service-spec.md`](service-spec.md)

기획 문서의 범위·경계·상세 조건·결정 대기 항목에 하네스가 요구하는 필수 표식이 있는지 확인한다. 하네스는 문서 내용의 정확성이나 제품 요구사항 충족 여부를 판정하지 않는다. 문서 변경 PR에는 관련 계약, 검사 결과와 사유, 영향과 남은 위험을 기록한다.

## 유지 규칙

1. 합의된 변경을 이 파일에 옮긴다.
2. API·상태·오류·데이터 구조가 확정되면 `packages/contracts/`를 생성하고, 이후 변경에는 해당 계약을 함께 수정한다.
3. 백엔드·AI 책임이나 실행 조건의 변경은 `docs/architecture/repository-structure.md`, `docs/architecture/repository-boundaries.md`와 하네스 기준을 함께 검토한다. 구체 경로는 관련 아키텍처 결정 뒤 추가한다.
4. 미확정 내용은 `결정 대기` 또는 결정 기록으로 남기고 확정된 것처럼 작성하지 않는다.
