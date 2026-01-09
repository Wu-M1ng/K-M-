import requests
import json
import uuid
import logging

logger = logging.getLogger(__name__)

KIRO_CODEWHISPERER_API = 'https://q.us-east-1.amazonaws.com/generateAssistantResponse'
KIRO_IDE_VERSION = '0.6.18'

KIRO_MODEL_MAP = {
    'kiro-pro': 'claude-sonnet-4.5',
    'kiro-flash': 'claude-haiku-4.5',
    'claude-sonnet-4-5': 'claude-sonnet-4.5',
    'claude-sonnet-4-5-20250929': 'claude-sonnet-4.5',
    'claude-haiku-4-5-20251001': 'claude-haiku-4.5',
    'claude-opus-4-5-20251101': 'claude-opus-4.5',
}

def get_kiro_headers(access_token, machine_id):
    invocation_id = str(uuid.uuid4())
    kiro_user_agent = f'KiroIDE-{KIRO_IDE_VERSION}-{machine_id}'
    
    return {
        'Content-Type': 'application/json',
        'x-amzn-codewhisperer-optout': 'true',
        'x-amzn-kiro-agent-mode': 'vibe',
        'x-amz-user-agent': f'aws-sdk-js/1.0.26 {kiro_user_agent}',
        'user-agent': f'aws-sdk-js/1.0.26 ua/2.1os/win32#10.0.26100 lang/js md/nodejs#22.21.1 api/codewhispererstreaming#1.0.26 m/E {kiro_user_agent}',
        'host': 'q.us-east-1.amazonaws.com',
        'amz-sdk-invocation-id': invocation_id,
        'amz-sdk-request': 'attempt=1; max=3',
        'Authorization': f'Bearer {access_token}'
    }

def convert_to_codewhisperer_messages(messages):
    cw_messages = []
    
    for msg in messages:
        role = msg.get('role')
        content = msg.get('content', '')
        
        if role == 'system':
            cw_messages.append({
                'role': 'system',
                'content': [{'text': content}] if isinstance(content, str) else content
            })
        elif role == 'user':
            cw_messages.append({
                'role': 'user',
                'content': [{'text': content}] if isinstance(content, str) else content
            })
        elif role == 'assistant':
            cw_messages.append({
                'role': 'assistant',
                'content': [{'text': content}] if isinstance(content, str) else content
            })
    
    return cw_messages

def convert_to_codewhisperer_request(messages, model='kiro-pro', max_tokens=4096):
    model_id = KIRO_MODEL_MAP.get(model, 'claude-sonnet-4.5')
    cw_messages = convert_to_codewhisperer_messages(messages)
    
    request_body = {
        'conversationState': {
            'currentMessage': {
                'userInputMessage': {
                    'content': cw_messages[-1]['content'] if cw_messages else [{'text': ''}],
                    'userInputMessageContext': {
                        'agentTaskType': 'vibe'
                    },
                    'userIntent': 'SUGGEST_ALTERNATE_IMPLEMENTATION'
                }
            },
            'chatTriggerType': 'MANUAL'
        },
        'modelConfiguration': {
            'modelId': model_id,
            'maxTokens': max_tokens
        },
        'profileArn': ''
    }
    
    if len(cw_messages) > 1:
        request_body['conversationState']['history'] = cw_messages[:-1]
    
    return request_body

def call_kiro_chat_stream(account, messages, model='kiro-pro', max_tokens=4096):
    credentials = account.get('credentials', {})
    access_token = credentials.get('accessToken')
    machine_id = account.get('machineId', 'default-machine-id')
    
    if not access_token:
        raise ValueError('No access token available')
    
    request_body = convert_to_codewhisperer_request(messages, model, max_tokens)
    headers = get_kiro_headers(access_token, machine_id)
    
    try:
        response = requests.post(
            KIRO_CODEWHISPERER_API,
            json=request_body,
            headers=headers,
            stream=True,
            timeout=60
        )
        
        if response.status_code != 200:
            error_text = response.text
            logger.error(f"Kiro API error: {response.status_code} - {error_text}")
            raise Exception(f"Kiro API error: {response.status_code}")
        
        for line in response.iter_lines():
            if line:
                yield line.decode('utf-8')
                
    except requests.exceptions.Timeout:
        raise Exception("Kiro API timeout")
    except Exception as e:
        logger.error(f"Kiro API call failed: {str(e)}")
        raise

def parse_kiro_stream_chunk(chunk_line):
    if not chunk_line or not chunk_line.strip():
        return None
    
    if chunk_line.startswith('data:'):
        data_str = chunk_line[5:].strip()
        if data_str:
            try:
                data = json.loads(data_str)
                
                if 'assistantResponseEvent' in data:
                    content = data['assistantResponseEvent'].get('content', '')
                    return {'type': 'content', 'text': content}
                elif 'codeReferenceEvent' in data:
                    return {'type': 'code_reference', 'data': data['codeReferenceEvent']}
                elif 'messageMetadataEvent' in data:
                    return {'type': 'metadata', 'data': data['messageMetadataEvent']}
                elif 'supplementaryWebLinksEvent' in data:
                    return {'type': 'web_links', 'data': data['supplementaryWebLinksEvent']}
                elif 'error' in data:
                    return {'type': 'error', 'error': data['error']}
                    
            except json.JSONDecodeError:
                pass
    
    return None
