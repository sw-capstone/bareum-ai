# bareum-ai — AI 처리 저장소

문서 처리와 AI 기능, 관련 테스트와 평가를 관리하는 저장소다. 공개 API는 `bareum-server`, 화면은 `bareum-web`에서 관리한다.

## 현재 구성

제품 AI 실행 코드와 평가 자산이 추가되기 전까지 저장소 운영에 필요한 문서와 정적 하네스만 관리한다.

- `.github/`: 협업 템플릿과 하네스 CI
- `docs/`: AI 저장소 경계, 개발 안내와 하네스 문서
- `harness/`: 저장소 정적 검사와 검사기 자체 테스트
- `scripts/`: 로컬과 CI의 공통 검사 명령

확정되지 않은 모델·프롬프트·데이터·평가 디렉터리는 미리 만들지 않는다. 실제 구현과 기준이 결정되면 해당 변경과 함께 구조와 검사를 추가한다.

## 현재 검사

```bash
./scripts/run-harness.sh check
./scripts/test-harness.sh
```

현재 하네스는 필수 경로, JSON 문법, Markdown 내부 링크와 비밀정보 의심 패턴을 검사한다. 비밀정보 검사는 설정·소스·문서에서 `API_KEY`, `CLIENT_SECRET`, `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD` 대입값(8자 이상)과 `sk-` 뒤 20자 이상 토큰 패턴을 찾고, `replace-me`, `example` 같은 예시값은 제외한다. 모든 비밀정보를 찾아내는 검사는 아니다. AI 결과의 정확성·성능과 서버 연동은 아직 검사하지 않는다.

## 저장소 경계

- 서버↔AI 형식은 담당자가 합의한 뒤 `bareum-server/packages/contracts/`에서 관리한다.
- AI 저장소는 합의된 계약을 소비하거나 제공하는 구현과 테스트를 담당한다.
- 다른 저장소의 문서나 파일을 복제해 별도의 기준으로 관리하지 않는다.
- 연동 검사는 계약과 실행 환경이 준비된 뒤 영향받는 저장소의 버전과 결과를 함께 기록하도록 확장한다.

## 문서

- [AI 개발 가이드](docs/guide/development-guide.md)
- [저장소 구조](docs/architecture/repository-structure.md)
- [하네스 설계 v1.4](docs/harness/harness-v1.4.md)
- [하네스 확장 로드맵](docs/harness/harness-file-roadmap.md)
