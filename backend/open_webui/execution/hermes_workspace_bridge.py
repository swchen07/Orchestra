"""Private stdio bridge to installed Hermes AIAgent (never a direct model stand-in)."""
import contextlib
import json
import os
from pathlib import Path
import sys
import threading

try:
    from open_webui.execution.trajectory import clean
except ModuleNotFoundError:
    from trajectory import clean

protocol = sys.stdout
_protocol_lock = threading.Lock()


def emit(kind, payload):
    with _protocol_lock:
        protocol.write(json.dumps({'kind': kind, 'payload': payload}, ensure_ascii=False) + '\n')
        protocol.flush()


def tool_callbacks(send=emit, failure_detector=None):
    """Installed Hermes calls start(id,name,args), complete(id,name,args,result)."""
    def start(tool_call_id, name, arguments):
        send('tool', {'id': str(tool_call_id)[:512], 'name': str(name)[:80],
                      'input': clean(arguments), 'done': False, 'status': 'running'})

    def complete(tool_call_id, name, arguments, function_result):
        result = function_result
        if isinstance(result, str) and len(result) <= 256 * 1024:
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                pass
        native = result if isinstance(result, dict) else {}
        exit_code = native.get('exit_code')
        error = native.get('error')
        status = native.get('status')
        if status in {'blocked', 'denied', 'declined', 'pending_approval'}:
            status = 'denied'
        elif status in {'cancelled', 'canceled'}:
            status = 'cancelled'
        elif failure_detector is not None:
            try:
                failed, suffix = failure_detector(str(name), function_result)
                status = 'failed' if failed else 'completed'
            except Exception:
                status = 'unknown'
        elif (error or status in {'failed', 'error', 'timeout'} or native.get('success') is False
              or isinstance(result, str) and result.startswith('Error executing tool ')):
            status = 'failed'
        else:
            status = 'unknown'  # Preserve native fields without guessing unavailable semantics.
        payload = {'id': str(tool_call_id)[:512], 'name': str(name)[:80], 'input': clean(arguments),
                   'output': clean(result), 'done': True, 'status': status,
                   'failure_classification': 'installed-hermes' if failure_detector is not None else 'native-fields-or-unknown'}
        if type(exit_code) in (int, float):
            payload['exit_code'] = exit_code
        if error:
            payload['error'] = clean(error)
        if isinstance(function_result, str) and len(function_result) > 16000:
            payload['output_truncated'] = True
        send('tool', payload)
    return start, complete


def main():
    packet = json.loads(sys.stdin.readline(2 * 1024 * 1024))
    source = packet['source']
    sys.path.insert(0, source)
    with contextlib.redirect_stdout(sys.stderr):
        from dotenv import load_dotenv
        load_dotenv(Path(packet['profile']) / '.env', override=True)
        # Explicitly scoped runtime/cwd overrides profile convenience defaults.
        os.environ.update(HERMES_HOME=packet['runtime'], TERMINAL_CWD=packet['workspace'], TERMINAL_ENV='local',
                          HERMES_INTERACTIVE='0', HERMES_QUIET='1')
        from run_agent import AIAgent
        try:
            from agent.display import _detect_tool_failure
        except ImportError:
            _detect_tool_failure = None
        start, complete = tool_callbacks(failure_detector=_detect_tool_failure)
        agent = AIAgent(model=packet['model'], provider=packet['provider'],
            enabled_toolsets=['terminal', 'file'], max_iterations=packet.get('max_iterations', 12),
            quiet_mode=True, verbose_logging=False, save_trajectories=False,
            skip_context_files=True, skip_memory=True, load_soul_identity=False,
            tool_start_callback=start, tool_complete_callback=complete,
            session_id=packet['session_id'], checkpoints_enabled=False)
        emit('metadata', {'actual_model': agent.model, 'native_session_id': agent.session_id,
            'context_mode': 'workspace-native-text-replay', 'native_tools': list(getattr(agent, 'valid_tool_names', [])),
            'observed_provider': packet['provider']})
        messages = packet['messages']
        history = [m for m in messages[:-1] if m.get('role') in {'user', 'assistant'}]
        system = '\n'.join(str(m.get('content', '')) for m in messages if m.get('role') == 'system')
        result = agent.run_conversation(str(messages[-1]['content']), system_message=system or None,
                                        conversation_history=history)
        prompt = int(getattr(agent, 'session_input_tokens', 0))
        cached = int(getattr(agent, 'session_cache_read_tokens', 0))
        written = int(getattr(agent, 'session_cache_write_tokens', 0))
        output = int(getattr(agent, 'session_output_tokens', 0))
        emit('usage', {'prompt_tokens': prompt + cached + written, 'completion_tokens': output,
                       'cache_read_tokens': cached, 'cache_write_tokens': written,
                       'native_entry_calls': 1, 'model_request_count': result.get('api_calls')})
        if result.get('interrupted') or result.get('error') or not result.get('final_response'):
            emit('error', {'message': 'Hermes native coding turn did not complete'})
            return 1
        emit('text', {'text': str(result['final_response'])})
        emit('done', {'model_request_count': result.get('api_calls')})
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        emit('error', {'message': 'Hermes workspace-native bridge failed; native details are private'})
        raise SystemExit(1)
