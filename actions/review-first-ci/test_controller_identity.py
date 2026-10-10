"""Exercise the actual App-owned state adapter, including the reported bypass."""
import copy
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from controller import Controller
from github_api import GitHub, GitHubHTTPError
from policy import GATE, MARKER, protected, eligible, current_review, BOT
from test_review_first import APP, BASE, CONFIG, HEAD, NEXT, REPO, RULES, FakeAPI, Harness, pull


class AppStore(FakeAPI, GitHub):
    state = GitHub.state
    save = GitHub.save
    gate = GitHub.gate
    checkpoint = GitHub.checkpoint
    decode_state = GitHub.decode_state
    owned = GitHub.owned
    write_check = GitHub.write_check

    def __init__(self):
        FakeAPI.__init__(self)
        self.records = {}
        self.discussion = []

    def checks(self, head):
        return self.check_results + [copy.deepcopy(c) for c in self.records.values() if c['head_sha'] == head]

    def comments(self, _):
        return copy.deepcopy(self.discussion)

    def request(self, path, method='GET', data=None):
        if path == 'check-runs':
            check = {**copy.deepcopy(data), 'id': len(self.records) + 100, 'app': {'id': APP}}
            self.records[check['id']] = check
            return copy.deepcopy(check)
        if path.startswith('check-runs/'):
            check = self.records[int(path.split('/')[1])]
            if method == 'PATCH':
                check.update(copy.deepcopy(data))
            return copy.deepcopy(check)
        if path == 'issues/1/comments':
            self.discussion.append({'id': len(self.discussion) + 1, **copy.deepcopy(data)})
            return {'id': len(self.discussion)}
        return FakeAPI.request(self, path, method, data)


class IdentityTests(unittest.TestCase):
    def test_shared_actions_and_missing_app_cannot_enable_deferral(self):
        for app in (0, 15368, None):
            rules = copy.deepcopy(RULES)
            rules[0]['parameters']['required_status_checks'][0]['integration_id'] = app
            self.assertFalse(protected(rules, app))
        self.assertFalse(protected(RULES, APP + 1))
        self.assertTrue(protected(RULES, APP))

    def test_check_write_must_authenticate_as_the_configured_app(self):
        api = GitHub('unused', REPO, APP)
        check = {'id': 10, 'app': {'id': 15368}, 'name': GATE,
                 'external_id': f'review-first:{REPO}:1'}
        with patch.object(api, 'checks', return_value=[]), patch.object(api, 'request', return_value=check):
            with self.assertRaisesRegex(RuntimeError, 'owned by the configured App'):
                api.gate(pull(), None, 'Pending')
            check['app']['id'] = APP
            api.gate(pull(), None, 'Pending')

    def test_pr_cannot_choose_an_unprotected_validation_branch(self):
        pr = pull()
        pr['base']['ref'] = 'untrusted-feature'
        self.assertFalse(eligible(pr, REPO, 'pilot'))
        api = FakeAPI()
        api.pull = pr
        with self.assertRaisesRegex(RuntimeError, 'default branch'):
            Harness(api, None, 'pilot', CONFIG).start(pr, {}, None, 'final')

    def test_forged_actions_comment_cannot_reuse_initial_runs_for_final_gate(self):
        api = AppStore()
        controller = Harness(api, None, 'pilot', CONFIG)
        controller.reconcile(1)
        api.finish()
        controller.reconcile(1)
        state, _ = api.state(api.pull, api.comments(1))
        self.assertTrue(state['baseline'])
        forged = {**state, 'phase': 'final', 'head': NEXT}
        api.discussion.append({'id': 999, 'user': {'login': 'github-actions[bot]'},
            'performed_via_github_app': {'id': 15368},
            'body': '<!-- review-first-ci:v1 -->\n```json\n' + json.dumps(forged) + '\n```'})
        # Even copying the new check format cannot impersonate the App.
        api.records[999] = {'id': 999, 'name': GATE, 'app': {'id': 15368}, 'head_sha': NEXT,
            'external_id': f'review-first:{REPO}:1', 'output': {'text': json.dumps(forged)}}
        api.discussion.append({'id': 1000, 'body': f'{MARKER}\ncheck: 999\n'})
        api.pull['head']['sha'] = NEXT
        controller.verdict = 'clean'
        controller.reconcile(1)
        state, check_id = api.state(api.pull, api.comments(1))
        self.assertEqual(state['phase'], 'final')
        self.assertNotEqual(state['ticket'], forged['ticket'])
        self.assertEqual(len(api.started), 4, 'Two genuine final workers are dispatched')
        self.assertEqual(api.records[check_id]['status'], 'in_progress')
        api.finish()
        controller.reconcile(1)
        self.assertEqual(api.records[check_id]['conclusion'], 'success')

    def test_current_checkpoint_wins_over_replayed_pointers_and_json(self):
        api = AppStore()
        original = {'version': 2, 'head': HEAD, 'base': BASE, 'phase': 'review', 'baseline': True}
        old_id = api.save(1, original, None)
        current = {**original, 'head': NEXT, 'phase': 'final', 'ticket': 'new-ticket'}
        new_id = api.save(1, current, None)
        api.pull['head']['sha'] = NEXT
        api.discussion = [{'body': f'{MARKER}\ncheck: {old_id}\n```json\n{{"phase":"final"}}\n```'}]
        self.assertEqual(api.state(api.pull, api.discussion), (current, new_id))

    def test_untrusted_missing_or_inaccessible_pointer_does_not_hide_real_state(self):
        for status in (403, 404):
            api = AppStore()
            state = {'version': 2, 'head': HEAD, 'phase': 'review', 'baseline': True}
            api.save(1, state, None)
            api.pull['head']['sha'] = NEXT
            api.discussion.append({'body': f'{MARKER}\ncheck: 999\n'})
            request = api.request
            def with_invalid_pointer(path, method='GET', data=None):
                if path == 'check-runs/999':
                    raise GitHubHTTPError('Unavailable check', status)
                return request(path, method, data)
            with patch.object(api, 'request', side_effect=with_invalid_pointer):
                self.assertEqual(api.state(api.pull, api.discussion), (state, None))

    def test_transient_lookup_failure_is_not_treated_as_absent_state(self):
        api = AppStore()
        api.discussion = [{'body': f'{MARKER}\ncheck: 999\n'}]
        with patch.object(api, 'request', side_effect=GitHubHTTPError('Unavailable', 503)):
            with self.assertRaises(GitHubHTTPError):
                api.state(api.pull, api.discussion)

    def test_uncomputed_mergeability_preserves_baseline_across_a_push(self):
        for baseline_passes in (True, False):
            api = AppStore()
            controller = Harness(api, None, 'pilot', CONFIG)
            controller.reconcile(1)
            api.finish('success' if baseline_passes else 'failure')
            controller.reconcile(1)
            api.pull['head']['sha'] = NEXT
            api.pull['mergeable_state'] = 'unknown'
            controller.reconcile(1)
            state, _ = api.state(api.pull, api.comments(1))
            self.assertEqual(state['baseline'], baseline_passes)
            self.assertEqual(state['head'], NEXT)
            self.assertEqual(len(api.started), 2)
            api.pull['mergeable_state'] = 'blocked'
            controller.reconcile(1)
            self.assertEqual(len(api.started), 2 if baseline_passes else 4)
            state, _ = api.state(api.pull, api.comments(1))
            self.assertEqual(state['phase'], 'review' if baseline_passes else 'initial')

    def test_failed_pointer_publication_does_not_strand_validation_dispatch(self):
        for phase in ('initial', 'final'):
            api = AppStore()
            controller = Harness(api, None, 'pilot', CONFIG)
            controller.verdict = 'clean'
            state = {'version': 2, 'baseline': phase == 'final', 'opened_head': HEAD}
            request = api.request
            def fail_pointer(path, method='GET', data=None):
                if path == 'issues/1/comments' and method == 'POST':
                    raise GitHubHTTPError('Comment service unavailable', 503)
                return request(path, method, data)
            with patch.object(api, 'request', side_effect=fail_pointer), self.assertLogs(level='WARNING'):
                controller.start(api.pull, state, None, phase)
            saved, check_id = api.state(api.pull, [])
            self.assertEqual(saved['dispatched'], list(CONFIG))
            self.assertEqual(len(api.started), 2)
            api.finish()
            controller.reconcile(1)
            saved, _ = api.state(api.pull, [])
            if phase == 'initial':
                self.assertTrue(saved['baseline'])
            else:
                self.assertEqual(api.records[check_id]['conclusion'], 'success')

    def test_same_commit_prs_keep_their_own_checkpoint_despite_latest_filter(self):
        api = AppStore()
        state = {'version': 2, 'head': HEAD, 'base': BASE, 'phase': 'review', 'baseline': True}
        original_id = api.save(1, state, None)
        api.save(2, state, None)
        def check_pages(path, key=None):
            records = list(api.records.values())
            if parse_qs(urlsplit(path).query).get('filter') != ['all']:
                records = [max(records, key=lambda c: c['id'])]
            return copy.deepcopy(records)
        with patch.object(api, 'checks', side_effect=lambda head: GitHub.checks(api, head)), \
                patch.object(api, 'pages', side_effect=check_pages):
            Harness(api, None, 'pilot', CONFIG).reconcile(1)
            self.assertEqual(api.state(api.pull, api.discussion), (state, original_id))
            self.assertEqual(len(api.records), 2)
            self.assertEqual(api.started, [])

    def test_state_cannot_be_imported_from_another_pr_or_app(self):
        api = AppStore()
        state = {'version': 2, 'head': HEAD, 'phase': 'final'}
        check_id = api.save(2, state, None)
        api.discussion = [{'body': f'{MARKER}\ncheck: {check_id}\n'}]
        self.assertEqual(api.state(api.pull, api.discussion), ({}, None))
        api.records[check_id]['external_id'] = f'review-first:{REPO}:1'
        api.records[check_id]['app']['id'] = 15368
        self.assertEqual(api.state(api.pull, api.discussion), ({}, None))

    def test_notification_bookkeeping_preserves_failed_gate(self):
        api = AppStore()
        controller = Harness(api, None, 'pilot', CONFIG)
        controller.reconcile(1)
        api.finish('failure')
        controller.reconcile(1)
        state, check_id = api.state(api.pull, api.comments(1))
        self.assertEqual(state['failure_reported'], state['ticket'])
        self.assertEqual(api.records[check_id]['conclusion'], 'failure')
        self.assertEqual(api.records[check_id]['status'], 'completed')

    def test_app_state_survives_classic_restore(self):
        api = AppStore()
        controller = Harness(api, None, 'pilot', CONFIG)
        controller.reconcile(1)
        api.finish()
        controller.reconcile(1)
        controller.mode = 'classic'
        controller.reconcile(1, restore=True)
        state, check_id = api.state(api.pull, api.comments(1))
        self.assertEqual(state['phase'], 'classic')
        self.assertEqual(api.records[check_id]['status'], 'in_progress')
        api.finish()
        controller.reconcile(1)
        self.assertEqual(api.records[check_id]['conclusion'], 'success')

    def test_inline_edit_invalidates_a_clean_review_until_new_activation(self):
        review = {'id': 3, 'user': {'login': BOT}, 'commit_id': HEAD,
                  'submitted_at': '2026-10-10T10:10:00Z', 'state': 'APPROVED', 'body': ''}
        inline = {'user': {'login': BOT}, 'commit_id': NEXT, 'created_at': '2026-10-10T10:01:00Z',
                  'updated_at': '2026-10-10T10:11:00Z'}
        state = {'opened_head': HEAD}
        self.assertEqual(current_review(pull(), state, [], [review], [inline], []), 'findings')
        state['requested'] = {'head': HEAD, 'at': '2026-10-10T10:12:00Z'}
        review['submitted_at'] = '2026-10-10T10:13:00Z'
        self.assertEqual(current_review(pull(), state, [], [review], [inline], []), 'clean')

    def test_clean_reaction_on_summary_comment_starts_validation(self):
        api = FakeAPI()
        summary = {'id': 40, 'user': {'login': BOT}, 'updated_at': '2026-10-10T10:05:00Z',
                   'body': '<!-- codex-pull-request-review-summary -->\n'
                           f'| **Code Review** | **Completed** | `{HEAD[:7]}` | PR opened |'}
        reaction = {'user': {'login': BOT}, 'content': '+1', 'created_at': '2026-10-10T10:05:01Z'}
        def pages(path, key=None):
            return [reaction] if path == 'issues/comments/40/reactions' else []
        with patch.object(api, 'pages', side_effect=pages) as calls:
            controller = Controller(api, None, 'pilot', CONFIG)
            self.assertEqual(controller.review(pull(), {'opened_head': HEAD}, [summary]), 'clean')
            self.assertIn(('issues/comments/40/reactions',), [c.args for c in calls.call_args_list])

    def test_deleted_actors_neither_approve_nor_block_authenticated_review(self):
        for identity in ({'user': None}, {}):
            api = FakeAPI()
            body = '<!-- codex-pull-request-review-summary -->\n' \
                   f'| **Code Review** | **Completed** | `{HEAD[:7]}` | PR opened |'
            orphan = {**identity, 'id': 40, 'body': body, 'content': '+1',
                      'commit_id': HEAD, 'state': 'APPROVED',
                      'created_at': '2026-10-10T10:05:00Z',
                      'updated_at': '2026-10-10T10:05:00Z',
                      'submitted_at': '2026-10-10T10:05:00Z'}
            controller = Controller(api, None, 'pilot', CONFIG)
            state = {'opened_head': HEAD}
            with patch.object(api, 'pages', return_value=[orphan]):
                self.assertEqual(controller.review(pull(), state, [orphan]), 'missing')
            summary = {**orphan, 'id': 41, 'user': {'login': BOT}}
            reaction = {'user': {'login': BOT}, 'content': '+1',
                        'created_at': '2026-10-10T10:05:01Z'}
            def pages(path, key=None):
                return [orphan, reaction] if path.endswith('/reactions') else [orphan]
            with patch.object(api, 'pages', side_effect=pages):
                self.assertEqual(controller.review(pull(), state, [orphan, summary]), 'clean')

    def test_runner_interruption_after_claim_recovers_without_a_new_push(self):
        api = FakeAPI()
        controller = Harness(api, None, 'pilot', CONFIG)
        controller.reconcile(1)
        api.finish()
        controller.reconcile(1)
        api.data['request_intent'] = {'head': HEAD, 'gate_id': 10, 'at': '2026-10-10T10:05:00Z'}
        api.events = [{'id': 11, 'event': 'labeled', 'label': {'name': 'awaiting-codex-reping'}}]
        controller.verdict = 'missing'
        controller.reviewer = api
        controller.reconcile(1)
        self.assertEqual(len(api.notices), 1)
        self.assertNotIn('request_intent', api.data)
        self.assertEqual(api.data['requested']['head'], HEAD)
        controller.reconcile(1)
        self.assertEqual(len(api.notices), 1, 'Recovery must not duplicate a delivered request')


if __name__ == '__main__':
    unittest.main()
