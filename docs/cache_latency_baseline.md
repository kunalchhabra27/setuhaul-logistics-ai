# Cache latency baseline

The branch audit was completed before implementation on
`feature/fast-loading-using-redis`. Existing cache and webhook code was found;
it used unscoped keys and broad scan-based invalidation.

The available local test suite was used as the reproducible baseline:

- 63 tests passed.
- 7 scheduler tests failed before this change because the SQLite fixtures were
  passed to a Supabase repository (`sqlite3.Connection` has no `.table()`),
  unrelated to Redis.

Representative authenticated portal latency could not be measured in this
workspace before implementation because no running authenticated backend and
usable test bearer tokens were available. The existing request middleware
continues to report total request duration, while the cache layer reports
generic cache hit/miss/error/fallback events. Authentication, cache, Supabase,
and service timings should be captured in the deployment environment with
real tokens and representative data; CloudWatch, dashboards, LangSmith, and
load testing are intentionally outside this change.
