import os
import json
import re
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
import anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder='public', static_url_path='')
client = anthropic.Anthropic()

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


@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


@app.route('/api/inspire', methods=['POST'])
def inspire():
    data = request.get_json() or {}
    topic = data.get('topic', '').strip()
    niche = data.get('niche', 'Content Creator').strip()
    if not topic:
        return jsonify({'error': 'Topic is required'}), 400
    try:
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1000,
            messages=[{
                'role': 'user',
                'content': (
                    f'You are an Instagram story angle generator for a {niche}. '
                    f'Return ONLY a valid JSON array, no markdown, no explanation.\n\n'
                    f'Generate 6 distinct Instagram story angles for a {niche} about: "{topic}"\n'
                    f'Each angle takes a different perspective (personal story, industry myth, hot take, '
                    f'behind-the-scenes, framework, case study).\n\n'
                    f'Return exactly:\n'
                    f'[{{"title":"4-6 word angle title","hook":"Scroll-stopping opening line max 12 words",'
                    f'"angle":"One sentence on the unique perspective"}}]'
                )
            }]
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
def generate():
    data = request.get_json() or {}
    mode = data.get('mode', 'inspire')
    input_text = data.get('input', '').strip()
    length = data.get('length', 7)
    niche = data.get('niche', 'Content Creator').strip()

    if not input_text:
        return jsonify({'error': 'Input is required'}), 400

    plan = get_slide_plan(length)
    types = ' → '.join(plan)

    base = (
        f'You are an expert Instagram story scriptwriter for a {niche}. '
        f'Write punchy, scroll-stopping, emotionally resonant copy tailored to a {niche} audience. '
        f'Slide types: hook=attention-grabbing opener, pain=audience struggle, relate=empathy/shared experience, '
        f'shift=new perspective turning point, value=core insight/tip, proof=result or credibility, '
        f'cta=one clear specific action. '
        f'Rules: headline max 8 words punchy; body 2-3 short conversational sentences; '
        f'direction=1 brief visual/mood note for designer. '
        f'Return ONLY valid JSON array, no markdown.\n\n'
    )

    if mode == 'braindump':
        prompt = (
            base +
            f'Transform these raw thoughts into a polished {length}-slide story script '
            f'staying true to the voice:\n---\n{input_text}\n---\n\n'
            f'Use slide types in order: {types}\n\n'
            f'Return {len(plan)} slides:\n'
            f'[{{"type":"hook","headline":"max 8 words","body":"2-3 sentences","direction":"visual note"}}]'
        )
    else:
        prompt = (
            base +
            f'Create a {length}-slide story script about: "{input_text}"\n\n'
            f'Use slide types in order: {types}\n\n'
            f'Return {len(plan)} slides:\n'
            f'[{{"type":"hook","headline":"max 8 words","body":"2-3 sentences","direction":"visual note"}}]'
        )

    def stream():
        full = ''
        try:
            yield f"data: {json.dumps({'type': 'start', 'total': len(plan)})}\n\n"
            with client.messages.stream(
                model='claude-sonnet-4-20250514',
                max_tokens=1000,
                messages=[{'role': 'user', 'content': prompt}]
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
                    f'Identify the 2-4 most impactful, punchy words in this Instagram story slide text '
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f'✦ Story Engine running → http://localhost:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)
