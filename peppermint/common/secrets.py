"""Presentation masking; original values remain available for task execution."""
import re

CREDENTIAL = re.compile(r'\b(?:pass\s*word|passwd|passphrase|passcode|pin|api[ _-]?key|access[ _-]?token|secret[ _-]?key)\b', re.I)
REQUEST = re.compile(r'\b(?:enter|provide|type|supply|need|what is|what.s)\b', re.I)


def requested_secret(text):
    return bool(CREDENTIAL.search(text or '') and REQUEST.search(text or ''))


def history_secrets(messages):
    secrets = set()
    waiting = False
    for message in messages:
        role, content = message.get('role'), message.get('content', '') or ''
        if role == 'assistant':
            for call in message.get('tool_calls', []):
                function = call.get('function', {})
                if function.get('name') == 'ask_user':
                    waiting = requested_secret(function.get('arguments', {}).get('question', ''))
            if content and not message.get('internal'):
                waiting = requested_secret(content)
        elif role == 'user' and waiting:
            if content.strip():
                secrets.add(content.strip())
            waiting = False
        elif role == 'tool' and waiting and content.startswith('The user answered: '):
            value = content.removeprefix('The user answered: ').strip()
            if value:
                secrets.add(value)
    return secrets


def mask(value, secrets):
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                value = value.replace(secret, '********')
        return value
    if isinstance(value, list):
        return [mask(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: mask(item, secrets) for key, item in value.items()}
    return value
