from utils.logging_utils import global_logger
from openai import AzureOpenAI
from openai import _exceptions
#from chatbot.utils.request_limiter_tracker import request_limiter_tracker
from utils.env_vars import AZURE_OPENAI_BASE, AZURE_OPENAI_API_KEY, OPENAI_API_VERSION, CHOSEN_EMB_MODEL, CHOSEN_COMPLETION_MODEL, DEBUG, TEMPERATURE, ENVIRONMENT
#from chatbot.utils.session_context import get_session_id_or_default
from utils.predefined_messages import CONTENT_FILTER_JAILBREAK_RESPONSE, CONTENT_FILTER_OTHER_RESPONSE
from azure.identity import DefaultAzureCredential
import ast

SEED = 42

def build_content_filter_extractor():
    """Create reusable compiled extractor for maximum speed. Azure OpenAI Content Filter handler"""
    def extract_filter_reasons(error_string):
        d = ast.literal_eval(error_string.split(" - ", 1)[1])
        f = d['error']['innererror']['content_filter_result']
        return [k for k, v in f.items() if v['filtered']]
    
    return extract_filter_reasons
    
extract_filter_reasons = build_content_filter_extractor()


def get_azure_ad_token():
    """Token provider function for Azure AD authentication"""
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return token.token

try:
    if ENVIRONMENT == "CT":
        raise Exception("Not using managed identity on CT")
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_BASE,
        azure_ad_token_provider=get_azure_ad_token,
        api_version=OPENAI_API_VERSION 
    )
    
    # Test the managed identity connection with a simple LLM call
    test_response = client.chat.completions.create(
        model=CHOSEN_COMPLETION_MODEL,
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=10
    )
    global_logger.info("Managed identity authentication successful - test LLM call completed")
    
except:
    if ENVIRONMENT != "CT":
        global_logger.warning(f"Using API Keys instead of Managed Identity.")
    else:
        global_logger.error(f"Failed to connect to Azure OpenAI with managed identity, falling back to API Keys")
    try:
        client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_BASE,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=OPENAI_API_VERSION   
        )
    except:
        global_logger.error(f"Failed to connect to Azure OpenAI. Exiting")
        raise Exception

def get_openai_embedding(query, embedding_model=CHOSEN_EMB_MODEL):
    try:
        res = client.embeddings.create(input=query, model=embedding_model)
        embeddings = res.data[0].embedding
        #request_limiter_tracker.update_embedding_usage(get_session_id_or_default(), embedding_model, res.usage.total_tokens)

        return embeddings, False
    except Exception as e:
        global_logger.error(f"An error occurred whilst gettig embeddings: {e}")
        return [], True

def call_llm(messages, tools=None, model=CHOSEN_COMPLETION_MODEL):
    """
    Call Azure OpenAI with messages and optional tools.
    
    Args:
        messages: List of message dictionaries with 'role' and 'content' keys
        tools: Optional list of tool definitions for function calling
        
    Returns:
        Response from Azure OpenAI
    """


    try:
        kwargs = {
            "model": model,
            "messages": messages,
            "seed": 42,
            "temperature": TEMPERATURE
        }

        if DEBUG:
            global_logger.info(f"LLM call to {model} with temperatture: {TEMPERATURE}\n{messages}\n\n\n")
        
        if tools:
            kwargs["tools"] = tools
        
        response = client.chat.completions.create(**kwargs)

        if DEBUG:
            global_logger.info(f"LLM response:\n{response.choices[0].message}\n\n\n")
        

        input_tokens = response.usage.prompt_tokens
        cached_tokens = response.usage.prompt_tokens_details.cached_tokens if response.usage.prompt_tokens_details else 0
        output_tokens = response.usage.completion_tokens
        #request_limiter_tracker.update_usage(get_session_id_or_default(), model, input_tokens, output_tokens, cached_tokens)
        return response.choices[0].message, False
    
    except _exceptions.BadRequestError as e:
        if e.status_code == 400 and e.code == "content_filter":
            inner_error = e.message
            filter_flags = extract_filter_reasons(inner_error)
            if 'jailbreak' in filter_flags:
                global_logger.warning(f"!Jailbreak attempt detected ({filter_flags}): \n{messages[-1]}\n\n")
                return CONTENT_FILTER_JAILBREAK_RESPONSE, True
            else:
                global_logger.warning(f"!Content filter triggered ({filter_flags}): \n{messages[-1]}\n\n")
                return CONTENT_FILTER_OTHER_RESPONSE, True
        else:
            global_logger.error(f"Error in call_llm, non standard BadRequestError: {e}")
            raise e

    except Exception as e:
        global_logger.error(f"Error in call_llm: {e}")
        raise e