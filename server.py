import os
import json
import re
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='public', static_url_path='')
client = anthropic.Anthropic()

CLIENTS_DIR = Path(__file__).parent / 'clients'


def load_clients():
    registry = {}
    if not CLIENTS_DIR.exists():
        return registry
    for folder in CLIENTS_DIR.iterdir():
        if not folder.is_dir():
            continue
        cfg_path = folder / 'config.json'
        ctx_path = folder / 'engine-context.md'
        if not cfg_path.exists() or not ctx_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
            context = ctx_path.read_text(encoding='utf-8')
            bible_path = folder / 'story-bible.md'
            if bible_path.exists():
                context += '\n\n---\n\n# Story Bible (Full Chapter Reference)\n\n'
                context += bible_path.read_text(encoding='utf-8')
            cfg['context'] = context
            registry[cfg['id']] = cfg
        except Exception as e:
            print(f'Failed to load client {folder.name}: {e}')
    return registry


CLIENTS = load_clients()
print(f'✦ Loaded {len(CLIENTS)} clients: {", ".join(CLIENTS.keys()) or "(none)"}')


def verify_client_password(client_id, password):
    cfg = CLIENTS.get(client_id)
    if not cfg:
        return False
    expected = os.environ.get(cfg.get('password_env', ''), '')
    if not expected:
        return False
    return password == expected


def get_client_id_and_context(req):
    """Pull client_id + password from request, verify, return (client_id, context_str) or (None, None)."""
    client_id = req.headers.get('X-Client-Id', '').strip().lower()
    password = req.headers.get('X-Client-Password', '').strip()
    if not client_id or not password:
        return None, None
    if not verify_client_password(client_id, password):
        return None, None
    cfg = CLIENTS.get(client_id)
    return client_id, cfg.get('context', '')


def require_client(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        client_id, context = get_client_id_and_context(request)
        if not client_id:
            return jsonify({'error': 'Unauthorized — invalid client or password'}), 401
        request.client_id = client_id
        request.client_context = context
        return fn(*args, **kwargs)
    return wrapper


SLIDE_PLANS = {
    4:  ['hook', 'pain', 'value', 'cta'],
    5:  ['hook', 'pain', 'relate', 'value', 'cta'],
    6:  ['hook', 'pain', 'relate', 'shift', 'value', 'cta'],
    7:  ['hook', 'pain', 'relate', 'shift', 'value', 'proof', 'cta'],
    8:  ['hook', 'pain', 'relate', 'shift', 'value', 'value', 'proof', 'cta'],
    9:  ['hook', 'pain', 'relate', 'relate', 'shift', 'value', 'value', 'proof', 'cta'],
    10: ['hook', 'pain', 'relate', 'relate', 'shift', 'value', 'value', 'proof', 'proof', 'cta'],
}


def get_slide_plan(length):
    return SLIDE_PLANS.get(max(4, min(10, int(length))), SLIDE_PLANS[7])


def build_system_message(client_context, niche):
    """Build the system message with prompt caching on the client context block."""
    base_role = (
        f'You are an expert Instagram carousel scriptwriter for a {niche}. '
        f'Write punchy, scroll-stopping, emotionally resonant copy. '
        f'Slide types: hook=attention-grabbing opener, pain=audience struggle, '
        f'relate=empathy/shared experience, shift=new perspective turning point, '
        f'value=core insight/tip, proof=result or credibility, cta=one clear specific action. '
        f'Rules: headline max 8 words punchy; body 2-3 short conversational sentences; '
        f'direction=1 brief visual/mood note for designer. '
        f'\n\nNON-NEGOTIABLE: the client context above defines HARD BANS, ICP filters, voice rules, '
        f'and (when present) a 9-question hook scoring framework. Apply all of them verbatim. '
        f'If the client bans em-dashes, verify every headline, body, and direction is em-dash free '
        f'before returning. If the client defines a hook scoring framework, score the hook slide '
        f'and put the score breakdown in the hook slide\'s direction field. '
        f'If the client defines an ICP, reject angles that target the wrong audience. '
        f'Pull receipts from the client credibility vault and story bible. Do not invent facts.'
    )
    blocks = []
    if client_context:
        blocks.append({
            'type': 'text',
            'text': client_context,
            'cache_control': {'type': 'ephemeral'},
        })
    blocks.append({'type': 'text', 'text': base_role})
    return blocks


@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


@app.route('/api/clients', methods=['GET'])
def list_clients():
    """Public endpoint — returns client list for login dropdown. No context, no passwords."""
    return jsonify({
        'clients': [
            {
                'id': c['id'],
                'name': c['name'],
                'niche': c.get('niche', ''),
                'default_length': c.get('default_length', 8),
                'default_language': c.get('default_language', 'English'),
                'trigger_words': c.get('trigger_words', []),
            }
            for c in CLIENTS.values()
        ]
    })


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    client_id = (data.get('client_id') or '').strip().lower()
    password = (data.get('password') or '').strip()
    if not verify_client_password(client_id, password):
        return jsonify({'error': 'Invalid client or password'}), 401
    cfg = CLIENTS[client_id]
    return jsonify({
        'ok': True,
        'client': {
            'id': cfg['id'],
            'name': cfg['name'],
            'niche': cfg.get('niche', ''),
            'default_length': cfg.get('default_length', 8),
            'default_language': cfg.get('default_language', 'English'),
            'trigger_words': cfg.get('trigger_words', []),
        }
    })


@app.route('/api/inspire', methods=['POST'])
@require_client
def inspire():
    data = request.get_json() or {}
    topic = data.get('topic', '').strip()
    niche = data.get('niche', 'Content Creator').strip()
    if not topic:
        return jsonify({'error': 'Topic is required'}), 400
    research_context = data.get('researchContext', '').strip()

    research_inject = ''
    if research_context:
        research_inject = f'\n\nCURRENT TREND INSIGHTS (use these to inform angles):\n{research_context}\n'

    system_blocks = build_system_message(request.client_context, niche)

    user_prompt = (
        f'Generate 6 distinct Instagram carousel angles for a {niche} about: "{topic}"\n'
        f'Each angle takes a different perspective (personal story, industry myth, hot take, '
        f'behind-the-scenes, framework, case study). '
        f'Honour the client context above — voice, ICP, beliefs, vocabulary.'
        f'{research_inject}\n\n'
        f'Return ONLY a valid JSON array, no markdown, no explanation:\n'
        f'[{{"title":"4-6 word angle title","hook":"Scroll-stopping opening line max 12 words",'
        f'"angle":"One sentence on the unique perspective"}}]'
    )

    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1000,
            system=system_blocks,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        text = response.content[0].text.strip()
        m = re.search(r'\[[\s\S]*\]', text)
        if not m:
            raise ValueError('Could not parse ideas')
        return jsonify({'ideas': json.loads(m.group(0))})
    except Exception as e:
        print(f'Inspire error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate', methods=['POST'])
@require_client
def generate():
    data = request.get_json() or {}
    mode = data.get('mode', 'inspire')
    input_text = data.get('input', '').strip()
    length = data.get('length', 7)
    niche = data.get('niche', 'Content Creator').strip()
    voice_style = data.get('voiceStyle', '').strip()

    if not input_text:
        return jsonify({'error': 'Input is required'}), 400

    plan = get_slide_plan(length)
    types = ' → '.join(plan)

    system_blocks = build_system_message(request.client_context, niche)

    voice_clause = (
        f'\n\nADDITIONAL VOICE OVERRIDE (use on top of client voice rules): {voice_style}'
        if voice_style else ''
    )

    if mode == 'braindump':
        user_prompt = (
            f'Transform these raw thoughts into a polished {length}-slide carousel script, '
            f'staying true to the client voice and using their ICP language:\n---\n{input_text}\n---\n\n'
            f'Use slide types in order: {types}\n{voice_clause}\n\n'
            f'Return ONLY valid JSON array, no markdown. {len(plan)} slides:\n'
            f'[{{"type":"hook","headline":"max 8 words","body":"2-3 sentences","direction":"visual note"}}]'
        )
    else:
        user_prompt = (
            f'Create a {length}-slide carousel script about: "{input_text}"\n\n'
            f'Use slide types in order: {types}\n'
            f'Pull from the client credibility vault and ICP language above. '
            f'Use real numbers / real names from the context — do not invent any.{voice_clause}\n\n'
            f'Return ONLY valid JSON array, no markdown. {len(plan)} slides:\n'
            f'[{{"type":"hook","headline":"max 8 words","body":"2-3 sentences","direction":"visual note"}}]'
        )

    def stream():
        full = ''
        try:
            yield f"data: {json.dumps({'type': 'start', 'total': len(plan)})}\n\n"
            with client.messages.stream(
                model='claude-sonnet-4-20250514',
                max_tokens=1500,
                system=system_blocks,
                messages=[{'role': 'user', 'content': user_prompt}],
            ) as s:
                for ev in s:
                    if ev.type == 'content_block_delta' and ev.delta.type == 'text_delta':
                        full += ev.delta.text
                        yield f"data: {json.dumps({'type': 'chunk', 'text': ev.delta.text})}\n\n"
            m = re.search(r'\[[\s\S]*\]', full)
            if m:
                yield f"data: {json.dumps({'type': 'complete', 'slides': json.loads(m.group(0))})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Parse failed — try again'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield 'data: [DONE]\n\n'

    return Response(
        stream_with_context(stream()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/api/highlight', methods=['POST'])
@require_client
def highlight():
    data = request.get_json() or {}
    text = data.get('text', '').strip()
    color = data.get('color', '#FF6B00')
    if not text:
        return jsonify({'error': 'Text is required'}), 400
    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=200,
            messages=[{
                'role': 'user',
                'content': (
                    f'Identify the 2-4 most impactful, punchy words in this Instagram carousel slide text '
                    f'that would look great highlighted in {color}. '
                    f'Choose words that carry maximum emotional or persuasive weight — not filler words.\n\n'
                    f'Text: "{text}"\n\n'
                    f'Return ONLY a JSON array of the exact words (lowercase), no explanation:\n'
                    f'["word1","word2","word3"]'
                )
            }]
        )
        raw = response.content[0].text.strip()
        m = re.search(r'\[[\s\S]*?\]', raw)
        if not m:
            raise ValueError('Could not parse word list')
        words = json.loads(m.group(0))
        return jsonify({'words': [str(w).lower() for w in words if w]})
    except Exception as e:
        print(f'Highlight error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/translate', methods=['POST'])
@require_client
def translate():
    data = request.get_json() or {}
    slides = data.get('slides', [])
    language = data.get('language', 'English')
    niche = data.get('niche', 'Content Creator').strip()
    if not slides or language == 'English':
        return jsonify({'slides': slides})
    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=2000,
            system=build_system_message(request.client_context, niche),
            messages=[{
                'role': 'user',
                'content': (
                    f'Translate these Instagram carousel slides into {language}. '
                    f'Preserve the punchy tone, energy, and formatting. Keep text concise. '
                    f'Honour the client voice rules and signature vocabulary above — '
                    f'never translate signal words that should stay in the original language.\n'
                    f'Return ONLY a valid JSON array with identical structure '
                    f'(same keys: type, headline, body, direction), no markdown.\n\n'
                    f'{json.dumps(slides)}'
                )
            }]
        )
        text = response.content[0].text.strip()
        m = re.search(r'\[[\s\S]*\]', text)
        if not m:
            raise ValueError('Could not parse translation')
        return jsonify({'slides': json.loads(m.group(0))})
    except Exception as e:
        print(f'Translate error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/voice', methods=['POST'])
@require_client
def voice():
    data = request.get_json() or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'error': 'Text is required'}), 400
    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=600,
            messages=[{
                'role': 'user',
                'content': (
                    f'Analyse this person\'s writing/speaking voice. '
                    f'Return ONLY a valid JSON object with these exact keys:\n'
                    f'"tone": "2-4 word descriptor",\n'
                    f'"rhythm": "2-4 word descriptor",\n'
                    f'"vocabulary": "2-4 word descriptor",\n'
                    f'"energy": "2-4 word descriptor",\n'
                    f'"style_notes": "one sentence max 20 words",\n'
                    f'"prompt_injection": "max 50 word paragraph to inject into carousel generation prompts to match this voice"\n\n'
                    f'Text to analyse:\n---\n{text}\n---'
                )
            }]
        )
        raw = response.content[0].text.strip()
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            raise ValueError('Could not parse voice profile')
        return jsonify({'profile': json.loads(m.group(0))})
    except Exception as e:
        print(f'Voice error: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/research', methods=['POST'])
@require_client
def research():
    data = request.get_json() or {}
    niche = data.get('niche', 'Content Creator').strip()
    topic = data.get('topic', '').strip()
    topic_clause = f' related to: {topic}' if topic else ''
    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1200,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3}],
            messages=[{
                'role': 'user',
                'content': (
                    f'Search for what Instagram carousel formats, hooks, and copy styles are currently '
                    f'performing best for {niche} creators in 2026{topic_clause}. '
                    f'After researching, return ONLY a valid JSON object with:\n'
                    f'"insights": ["3-4 short trend observations, each max 15 words"],\n'
                    f'"hook_styles": ["2-3 effective hook formats currently working"],\n'
                    f'"inject": "max 60 word paragraph summarising current trends to inject into idea generation"'
                )
            }]
        )
        text = ''
        for block in response.content:
            if hasattr(block, 'text'):
                text += block.text
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            raise ValueError('Could not parse research')
        return jsonify({'research': json.loads(m.group(0))})
    except Exception as e:
        print(f'Research error: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f'✦ Story Engine running → http://localhost:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)
