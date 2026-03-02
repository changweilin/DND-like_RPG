import json
import ollama
from engine.config import config

class LLMClient:
    def __init__(self, model_name=None):
        self.model = model_name or config.LLM_MODEL_NAME
        
    def generate_turn(self, system_prompt, user_action, context=""):
        """Generates the narrative, choices, and mechanics in a single JSON response."""
        
        json_instruction = """
CRITICAL INSTRUCTION: You MUST respond ONLY with a valid JSON object. Do not include markdown blocks, just the raw JSON.
Your JSON must strictly follow this schema:
{
  "narrative": "The atmospheric description of what happens next (string).",
  "choices": ["Choice 1 (e.g. Talk to merchant)", "Choice 2 (e.g. Draw your sword)", "Choice 3 (e.g. Leave the cave)"],
  "damage_taken": 0,
  "hp_healed": 0,
  "items_found": []
}
"""
        full_system_prompt = f"{system_prompt}\n\nRelevant Context from Memory:\n{context}\n\n{json_instruction}"
        
        try:
            response = ollama.chat(
                model=self.model, 
                messages=[
                    {"role": "system", "content": full_system_prompt},
                    {"role": "user", "content": f"I choose: {user_action}"}
                ],
                format='json'
            )
            return json.loads(response.message.content)
        except Exception as e:
            print(f"Error parsing JSON from LLM: {e}")
            return {
                "narrative": "There is a strange silence in the universe... (Error generating response)",
                "choices": ["Try to look around again", "Wait a moment"],
                "damage_taken": 0, "hp_healed": 0, "items_found": []
            }
