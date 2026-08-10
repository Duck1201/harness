import asyncio

from harness.page_verification import (
    BrowserVerification,
    BrowserVerificationRequest,
    PageRevisionKey,
    PageStatus,
    PageVerificationAutomation,
    PageVerificationExecutionStatus,
    PageVerificationMode,
    PageVerificationResult,
)


class RecordingBrowserVerifier:
    def __init__(self) -> None:
        self.requests: list[BrowserVerificationRequest] = []

    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification:
        self.requests.append(request)
        return BrowserVerification(page_status=PageStatus.VALID)


class FailingBrowserVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification:
        del request
        self.calls += 1
        raise RuntimeError("browser context crashed")


class CancellingBrowserVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification:
        del request
        self.calls += 1
        raise asyncio.CancelledError


class VerboseBrowserVerifier:
    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification:
        del request
        return BrowserVerification(
            page_status=PageStatus.INVALID,
            diagnostics=("0123456789extra", "abcdefghijk", "not returned"),
        )


class ControlledBrowserVerifier:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def verify(self, request: BrowserVerificationRequest) -> BrowserVerification:
        del request
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return BrowserVerification(page_status=PageStatus.VALID)


def test_page_revision_digest_is_deterministic_and_uses_full_identity() -> None:
    key = PageRevisionKey(
        workspace_id="workspace-a",
        relative_path="index.html",
        html_sha256="html-a",
        workspace_revision=7,
        verifier_digest="verifier-a",
    )

    assert key.digest == "b1975ba883c0036a0f358277557a435224109fadedbd080cef30350ae83f7aa4"
    assert (
        key.digest
        == PageRevisionKey(
            workspace_id="workspace-a",
            relative_path="index.html",
            html_sha256="html-a",
            workspace_revision=7,
            verifier_digest="verifier-a",
        ).digest
    )
    assert (
        len(
            {
                key.digest,
                PageRevisionKey("workspace-b", "index.html", "html-a", 7, "verifier-a").digest,
                PageRevisionKey("workspace-a", "other.html", "html-a", 7, "verifier-a").digest,
                PageRevisionKey("workspace-a", "index.html", "html-a", 7, "verifier-b").digest,
            }
        )
        == 4
    )


def test_disabled_mode_does_not_call_browser_or_inject_result() -> None:
    async def scenario() -> None:
        browser = RecordingBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.DISABLED,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        result = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<h1>Hello</h1>",
            workspace_revision=1,
            changed=True,
        )

        assert result.execution_status is PageVerificationExecutionStatus.DISABLED
        assert result.page_status is None
        assert result.diagnostics == ()
        assert result.inject_result is False
        assert browser.requests == []

    asyncio.run(scenario())


def test_automatic_mode_executes_and_injects_only_once_per_page_revision() -> None:
    async def scenario() -> None:
        browser = RecordingBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        async def verify() -> PageVerificationResult:
            return await automation.verify_after_write(
                workspace_id="workspace-a",
                relative_path="index.html",
                html=b"<h1>Hello</h1>",
                workspace_revision=1,
                changed=True,
            )

        first = await verify()
        second = await verify()

        assert first.execution_status is PageVerificationExecutionStatus.EXECUTED
        assert first.page_status is PageStatus.VALID
        assert first.inject_result is True
        assert second.execution_status is PageVerificationExecutionStatus.CACHED
        assert second.page_status is PageStatus.VALID
        assert second.inject_result is False
        assert len(browser.requests) == 1
        assert browser.requests[0].html == b"<h1>Hello</h1>"
        assert browser.requests[0].page_revision == first.page_revision

    asyncio.run(scenario())


def test_unchanged_write_does_not_create_or_verify_a_page_revision() -> None:
    async def scenario() -> None:
        browser = RecordingBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        result = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<h1>Same bytes</h1>",
            workspace_revision=4,
            changed=False,
        )

        assert result.execution_status is PageVerificationExecutionStatus.SKIPPED_UNCHANGED
        assert result.page_revision is None
        assert result.inject_result is False
        assert browser.requests == []

    asyncio.run(scenario())


def test_revision_or_html_hash_change_creates_a_distinct_page_revision() -> None:
    async def scenario() -> None:
        browser = RecordingBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        first = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<h1>First</h1>",
            workspace_revision=1,
            changed=True,
        )
        new_revision = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<h1>First</h1>",
            workspace_revision=2,
            changed=True,
        )
        new_hash = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<h1>Second</h1>",
            workspace_revision=2,
            changed=True,
        )

        assert len(browser.requests) == 3
        assert (
            len(
                {
                    first.page_revision,
                    new_revision.page_revision,
                    new_hash.page_revision,
                }
            )
            == 3
        )

    asyncio.run(scenario())


def test_browser_failure_is_a_separate_cached_automation_result() -> None:
    async def scenario() -> None:
        browser = FailingBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        first = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<main>Already written</main>",
            workspace_revision=3,
            changed=True,
        )
        cached = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<main>Already written</main>",
            workspace_revision=3,
            changed=True,
        )

        assert first.execution_status is PageVerificationExecutionStatus.FAILED
        assert first.page_status is None
        assert first.diagnostics == ("RuntimeError: browser context crashed",)
        assert first.inject_result is True
        assert cached.execution_status is PageVerificationExecutionStatus.CACHED
        assert cached.diagnostics == first.diagnostics
        assert cached.inject_result is False
        assert browser.calls == 1

    asyncio.run(scenario())


def test_browser_cancellation_returns_its_own_cached_result() -> None:
    async def scenario() -> None:
        browser = CancellingBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        first = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<main>Already written</main>",
            workspace_revision=4,
            changed=True,
        )
        cached = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<main>Already written</main>",
            workspace_revision=4,
            changed=True,
        )

        assert first.execution_status is PageVerificationExecutionStatus.CANCELLED
        assert first.page_status is None
        assert first.diagnostics == ()
        assert first.inject_result is True
        assert cached.execution_status is PageVerificationExecutionStatus.CACHED
        assert cached.inject_result is False
        assert browser.calls == 1

    asyncio.run(scenario())


def test_page_status_is_bounded_and_diagnostics_are_limited() -> None:
    async def scenario() -> None:
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=VerboseBrowserVerifier(),
            verifier_digest="verifier-a",
            max_diagnostics=2,
            max_diagnostic_chars=10,
        )

        result = await automation.verify_after_write(
            workspace_id="workspace-a",
            relative_path="index.html",
            html=b"<main>Invalid</main>",
            workspace_revision=5,
            changed=True,
        )

        assert result.page_status is PageStatus.INVALID
        assert result.diagnostics == ("0123456789", "abcdefghij")

    asyncio.run(scenario())


def test_concurrent_requests_execute_and_inject_once_for_the_same_key() -> None:
    async def scenario() -> None:
        browser = ControlledBrowserVerifier()
        automation = PageVerificationAutomation(
            mode=PageVerificationMode.AUTOMATIC_ONCE_PER_PAGE_REVISION,
            verifier=browser,
            verifier_digest="verifier-a",
        )

        async def verify() -> PageVerificationResult:
            return await automation.verify_after_write(
                workspace_id="workspace-a",
                relative_path="index.html",
                html=b"<main>Concurrent</main>",
                workspace_revision=6,
                changed=True,
            )

        first_task = asyncio.create_task(verify())
        await browser.started.wait()
        second_task = asyncio.create_task(verify())
        await asyncio.sleep(0)

        assert browser.calls == 1
        browser.release.set()
        first, second = await asyncio.gather(first_task, second_task)
        assert {
            first.execution_status,
            second.execution_status,
        } == {
            PageVerificationExecutionStatus.EXECUTED,
            PageVerificationExecutionStatus.CACHED,
        }
        assert sum((first.inject_result, second.inject_result)) == 1

    asyncio.run(scenario())
