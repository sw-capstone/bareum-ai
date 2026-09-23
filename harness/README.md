# AI 저장소 하네스

`bareum-server-ai` 내부의 구조와 문서 기본 품질을 검사한다.

```bash
./scripts/run-harness.sh check
./scripts/test-harness.sh
```

현재 검사 항목은 다음과 같다.

- 필수 경로 존재 여부
- JSON 문법
- Markdown 내부 링크
- 비밀정보 의심 패턴

검사 결과는 `harness/reports/report.json`에 생성되고 Git에는 저장하지 않는다. GitHub Actions는 Push와 Pull Request에서 같은 명령을 실행하고 결과 파일을 `harness-report` 아티팩트로 제공한다.

제품 AI 코드·계약·평가 기준이 추가되면 해당 담당자가 검사를 작성하고 하네스 담당자의 리뷰를 받아 확장한다.

- [하네스 설계 v1.4](../docs/harness/harness-v1.4.md)
- [확장 로드맵](../docs/harness/harness-file-roadmap.md)
