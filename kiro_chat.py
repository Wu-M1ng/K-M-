import uuid
import json
import time
import logging
import struct
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

KIRO_MODEL_MAP = {
    'claude-sonnet-4-5': 'claude-sonnet-4.5',
    'claude-sonnet-4-5-20250929': 'claude-sonnet-4.5',
    'claude-sonnet-4-20250514': 'claude-sonnet-4',
    'claude-opus-4-5-20251101': 'claude-opus-4.5',
    'claude-haiku-4-5-20251001': 'claude-haiku-4.5'
}

KIRO_DEFAULTS = {
    'MAX_TOKENS': 1048576,
    'AGENT_TASK_TYPE': 'vibe',
    'CHAT_TRIGGER_TYPE': 'MANUAL',
    'ORIGIN': 'AI_EDITOR'
}

KIRO_IDE_VERSION = '0.6.18'

class KiroChatClient:
    def __init__(self, account_manager):
        self.account_manager = account_manager

    def get_kiro_model_id(self, model):
        return KIRO_MODEL_MAP.get(model, model)

    def extract_text_content(self, content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join([
                block.get('text', '') 
                for block in content 
                if block.get('type') == 'text'
            ])
        return ""

    def extract_images(self, content):
        if not isinstance(content, list):
            return []
        
        images = []
        for block in content:
            if block.get('type') == 'image_url' and block.get('image_url'):
                url = block['image_url'].get('url', '')
                if url.startswith('data:'):
                    try:
                        header, base64_data = url.split(',', 1)
                        mime_type = header.split(':')[1].split(';')[0]
                        fmt = mime_type.split('/')[1]
                        if fmt == 'jpeg':
                            fmt = 'jpeg'
                        
                        images.append({
                            'format': fmt,
                            'source': {'bytes': base64_data}
                        })
                    except Exception as e:
                        logger.warning(f"Failed to parse base64 image: {e}")
        return images

    def convert_to_codewhisperer_request(self, messages, model, options=None):
        options = options or {}
        model_id = self.get_kiro_model_id(model)
        conversation_id = str(uuid.uuid4())
        agent_continuation_id = str(uuid.uuid4())

        system_messages = [m for m in messages if m.get('role') == 'system']
        non_system_messages = [m for m in messages if m.get('role') != 'system']

        history = []

        if system_messages:
            system_content = "\n".join([
                self.extract_text_content(m.get('content')) 
                for m in system_messages
            ])
            history.append({
                'userInputMessage': {
                    'content': system_content,
                    'modelId': model_id,
                    'origin': KIRO_DEFAULTS['ORIGIN'],
                    'images': [],
                    'userInputMessageContext': {}
                }
            })
            history.append({
                'assistantResponseMessage': {
                    'content': 'OK',
                    'toolUses': None
                }
            })

        user_buffer = []
        for i in range(len(non_system_messages) - 1):
            msg = non_system_messages[i]
            role = msg.get('role')

            if role in ['user', 'tool']:
                user_buffer.append(msg)
            elif role == 'assistant':
                if user_buffer:
                    content = "\n".join([
                        self.extract_text_content(m.get('content')) 
                        for m in user_buffer if m.get('role') == 'user'
                    ])
                    
                    history.append({
                        'userInputMessage': {
                            'content': content,
                            'modelId': model_id,
                            'origin': KIRO_DEFAULTS['ORIGIN'],
                            'images': [],
                            'userInputMessageContext': {}
                        }
                    })
                    user_buffer = []

                text_content = self.extract_text_content(msg.get('content'))
                history.append({
                    'assistantResponseMessage': {
                        'content': text_content or '',
                        'toolUses': None
                    }
                })

        if user_buffer:
             content = "\n".join([
                self.extract_text_content(m.get('content')) 
                for m in user_buffer if m.get('role') == 'user'
            ])
             history.append({
                'userInputMessage': {
                    'content': content,
                    'modelId': model_id,
                    'origin': KIRO_DEFAULTS['ORIGIN'],
                    'images': [],
                    'userInputMessageContext': {}
                }
            })
             history.append({
                'assistantResponseMessage': {
                    'content': 'OK',
                    'toolUses': None
                }
            })

        if not non_system_messages:
             pass
        else:
            last_msg = non_system_messages[-1]
            current_content = self.extract_text_content(last_msg.get('content'))
            current_images = self.extract_images(last_msg.get('content'))
            
            user_input_context = {}

            return {
                'conversationState': {
                    'agentContinuationId': agent_continuation_id,
                    'agentTaskType': KIRO_DEFAULTS['AGENT_TASK_TYPE'],
                    'chatTriggerType': KIRO_DEFAULTS['CHAT_TRIGGER_TYPE'],
                    'currentMessage': {
                        'userInputMessage': {
                            'userInputMessageContext': user_input_context,
                            'content': current_content,
                            'modelId': model_id,
                            'images': current_images,
                            'origin': KIRO_DEFAULTS['ORIGIN']
                        }
                    },
                    'conversationId': conversation_id,
                    'history': history
                }
            }
        return {}

    def get_headers(self, access_token, machine_id):
        invocation_id = str(uuid.uuid4())
        kiro_user_agent = f"KiroIDE-{KIRO_IDE_VERSION}-{machine_id}"
        return {
            'Content-Type': 'application/json',
            'x-amzn-codewhisperer-optout': 'true',
            'x-amzn-kiro-agent-mode': 'vibe',
            'x-amz-user-agent': f"aws-sdk-js/1.0.26 {kiro_user_agent}",
            'user-agent': f"aws-sdk-js/1.0.26 ua/2.1os/win32#10.0.26100 lang/js md/nodejs#22.21.1 api/codewhispererstreaming#1.0.26 m/E {kiro_user_agent}",
            'host': 'q.us-east-1.amazonaws.com',
            'amz-sdk-invocation-id': invocation_id,
            'amz-sdk-request': 'attempt=1; max=3',
            'Authorization': f'Bearer {access_token}'
        }

    def parse_stream_message(self, buffer):
        if len(buffer) < 16:
            return None, 0
        
        total_length = struct.unpack('>I', buffer[0:4])[0]
        
        if total_length < 16 or total_length > 16 * 1024 * 1024:
            return None, 1
            
        if len(buffer) < total_length:
            return None, 0
            
        header_length = struct.unpack('>I', buffer[4:8])[0]
        
        payload_start = 12 + header_length
        payload_end = total_length - 4
        
        if payload_start > payload_end or payload_end > len(buffer):
             return None, 0
             
        payload_data = buffer[payload_start:payload_end]
        
        try:
            payload = json.loads(payload_data.decode('utf-8'))
            return payload, total_length
        except Exception as e:
            return None, total_length

    def stream_response(self, account, request_body):
        headers = self.get_headers(account['credentials']['accessToken'], account['machineId'])
        url = 'https://q.us-east-1.amazonaws.com/generateAssistantResponse'
        
        try:
            response = requests.post(url, headers=headers, json=request_body, stream=True, timeout=120)
            
            if response.status_code != 200:
                logger.error(f"Kiro API Error: {response.status_code} - {response.text}")
                yield {"error": f"Kiro API Error: {response.status_code}"}
                return

            buffer = bytearray()
            
            for chunk in response.iter_content(chunk_size=1024):
                if not chunk:
                    continue
                buffer.extend(chunk)
                
                while len(buffer) >= 16:
                    message, consumed = self.parse_stream_message(buffer)
                    if consumed == 0:
                        break
                        
                    del buffer[:consumed]
                    
                    if message:
                        if message.get('content'):
                            yield {"content": message['content']}
                        
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield {"error": str(e)}

