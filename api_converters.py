import json
import time
import uuid
from typing import Dict, Any, List, Optional, AsyncGenerator

def openai_to_kiro_messages(messages: List[Dict]) -> List[Dict]:
    kiro_messages = []
    for msg in messages:
        role = msg.get('role')
        content = msg.get('content')
        
        if role == 'system':
            kiro_messages.append({'role': 'system', 'content': content})
        elif role == 'user':
            kiro_messages.append({'role': 'user', 'content': content})
        elif role == 'assistant':
            kiro_messages.append({'role': 'assistant', 'content': content})
    
    return kiro_messages

def anthropic_to_openai_messages(messages: List[Dict], system: Optional[str] = None) -> List[Dict]:
    openai_messages = []
    
    if system:
        openai_messages.append({'role': 'system', 'content': system})
    
    for msg in messages:
        role = msg.get('role')
        content = msg.get('content')
        
        if isinstance(content, str):
            text_content = content
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get('type') == 'text':
                        text_parts.append(block.get('text', ''))
                elif hasattr(block, 'text'):
                    text_parts.append(block.text)
            text_content = '\n'.join(text_parts)
        else:
            text_content = str(content)
        
        openai_messages.append({'role': role, 'content': text_content})
    
    return openai_messages

def create_openai_chunk(content: str, model: str, finish_reason: Optional[str] = None) -> str:
    chunk = {
        'id': f'chatcmpl-{uuid.uuid4().hex[:24]}',
        'object': 'chat.completion.chunk',
        'created': int(time.time()),
        'model': model,
        'choices': [{
            'index': 0,
            'delta': {'content': content} if content else {},
            'finish_reason': finish_reason
        }]
    }
    return f'data: {json.dumps(chunk)}\n\n'

def create_openai_response(content: str, model: str, input_tokens: int = 0, output_tokens: int = 0) -> Dict:
    return {
        'id': f'chatcmpl-{uuid.uuid4().hex[:24]}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': [{
            'index': 0,
            'message': {
                'role': 'assistant',
                'content': content
            },
            'finish_reason': 'stop'
        }],
        'usage': {
            'prompt_tokens': input_tokens,
            'completion_tokens': output_tokens,
            'total_tokens': input_tokens + output_tokens
        }
    }

def create_anthropic_chunk(content: str, model: str, message_id: str, finish_reason: Optional[str] = None) -> str:
    if finish_reason:
        event = {
            'type': 'message_delta',
            'delta': {'stop_reason': finish_reason}
        }
        return f'event: message_delta\ndata: {json.dumps(event)}\n\n'
    else:
        event = {
            'type': 'content_block_delta',
            'index': 0,
            'delta': {
                'type': 'text_delta',
                'text': content
            }
        }
        return f'event: content_block_delta\ndata: {json.dumps(event)}\n\n'

def create_anthropic_response(content: str, model: str, input_tokens: int = 0, output_tokens: int = 0) -> Dict:
    return {
        'id': f'msg_{uuid.uuid4().hex[:24]}',
        'type': 'message',
        'role': 'assistant',
        'content': [{
            'type': 'text',
            'text': content
        }],
        'model': model,
        'stop_reason': 'end_turn',
        'usage': {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens
        }
    }
