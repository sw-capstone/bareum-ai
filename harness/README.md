# AI 저장소 하네스

`bareum-ai` 내부의 구조와 문서 기본 품질을 검사한다.

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

## 정책 검증

`policy.json`은 `version`, `required_paths`, `checks`를 갖는 JSON 객체다. 버전은 비어 있지 않은 문자열, 필수 경로는 문자열 배열, 검사 설정은 지원하는 검사 이름과 Boolean 값으로 작성한다. 생략한 검사 이름은 기존 방식대로 활성화되므로 비활성화하려면 `false`를 명시한다.

알 수 없는 항목·검사 이름, 중복 JSON 키, 잘못된 자료형, 빈 경로와 저장소 밖을 가리키는 경로는 `HAR-POLICY-001`로 실패한다. 경로는 저장소 내부의 상대 경로를 사용하며, 외부를 가리키는 심볼릭 링크도 허용하지 않는다. 등록한 파일의 존재 여부는 필수 경로 검사가 확인한다. 비활성 검사에 남아 있는 설정도 형식과 경로를 검증한다. 자동 수정 설정의 자료형 확인은 자동 수정 기능의 실행을 뜻하지 않는다.

UTF-8로 표현할 수 없는 정책 문자열도 정책 오류로 처리하고 실패 리포트를 남긴다. 정상적인 한글과 이모지 문자열은 허용한다.

- [하네스 설계 v1.4](../docs/harness/harness-v1.4.md)
- [확장 로드맵](../docs/harness/harness-file-roadmap.md)
