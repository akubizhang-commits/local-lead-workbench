from leadworkbench.sources.social_browser import login_diagnostic


def test_diagnostics_never_include_login_identifiers_or_tokens():
    result = login_diagnostic('response', 'https://x.com/i/api/1.1/onboarding/task.json?token=secret&email=private', 403)
    assert result['host'] == 'x.com'
    assert result['area'] == 'onboarding'
    assert result['status'] == 403
    assert 'secret' not in str(result)
    assert 'private' not in str(result)
    assert 'task.json' not in str(result)


def test_diagnostics_redact_unknown_hosts_and_failure_text():
    result = login_diagnostic('request_failed', 'https://private-id.example.org/secret', 'net::ERR_TIMED_OUT password')
    assert result['host'] == 'other'
    assert result['failure'] == 'ERR_TIMED_OUT'
    assert 'password' not in str(result)


def test_native_login_uses_only_dedicated_profile_and_optional_proxy(tmp_path):
    from leadworkbench.sources.social_browser import native_login_command
    profile=tmp_path/'social-browser'/'x'
    args=native_login_command(profile,{})
    assert '--user-data-dir='+str(profile) in args
    assert args[-1]=='https://x.com/'
    assert not any('remote-debugging' in a or 'disable' in a or 'mock-keychain' in a for a in args)
    assert '--proxy-server=http://127.0.0.1:7890' in native_login_command(profile,{'proxy_url':'http://127.0.0.1:7890'})
