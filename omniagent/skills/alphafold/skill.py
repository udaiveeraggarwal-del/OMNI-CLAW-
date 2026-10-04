import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from omniagent.skills.base import BaseSkill
from omniagent.core.router import BaseTool
from omniagent.core.models import ToolDefinition, ToolResult

class FetchStructureTool(BaseTool):
    @property
    def name(self) -> str:
        return "alphafold.fetch_structure"
        
    @property
    def description(self) -> str:
        return "Fetch protein predicted structure metadata from AlphaFold DB."
        
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uniprot_id": {"type": "string", "description": "UniProt Accession ID"}
            },
            "required": ["uniprot_id"]
        }
        
    def execute(self, **kwargs) -> ToolResult:
        uniprot_id = kwargs.get("uniprot_id")
        if not uniprot_id:
            return ToolResult(success=False, error="uniprot_id is required")
            
        url = f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "OMNI-CLAW/1.0"})
        
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode("utf-8"))
                if not data:
                    return ToolResult(success=False, error="No data returned from AlphaFold")
                
                prediction = data[0]
                return ToolResult(success=True, output=json.dumps({
                    "uniprot_id": prediction.get("uniprotAccession"),
                    "organism": prediction.get("organismScientificName"),
                    "gene": prediction.get("gene"),
                    "model_url": prediction.get("pdbUrl"),
                    "cif_url": prediction.get("cifUrl"),
                    "sequence_length": len(prediction.get("uniprotSequence", "")),
                    "model_created_date": prediction.get("modelCreatedDate"),
                    "latest_version": prediction.get("latestVersion")
                }))
        except urllib.error.HTTPError as e:
            return ToolResult(success=False, error=f"AlphaFold API error: {e.code} - {e.reason}")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to fetch structure: {str(e)}")

class AnalyzeConfidenceTool(BaseTool):
    @property
    def name(self) -> str:
        return "alphafold.analyze_confidence"
        
    @property
    def description(self) -> str:
        return "Fetch and analyze pLDDT confidence scores for a protein."
        
    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "uniprot_id": {"type": "string", "description": "UniProt Accession ID"}
            },
            "required": ["uniprot_id"]
        }
        
    def execute(self, **kwargs) -> ToolResult:
        uniprot_id = kwargs.get("uniprot_id")
        if not uniprot_id:
            return ToolResult(success=False, error="uniprot_id is required")
            
        url = f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "OMNI-CLAW/1.0"})
        
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode("utf-8"))
                if not data:
                    return ToolResult(success=False, error="No data returned from AlphaFold")
                
                prediction = data[0]
                return ToolResult(success=True, output=json.dumps({
                    "uniprot_id": prediction.get("uniprotAccession"),
                    "confidence_assessment": "AlphaFold predictions are scored 0-100 (pLDDT). >90 is highly accurate, 70-90 is confident, 50-70 is low confidence, <50 is often intrinsically disordered.",
                    "pae_image_url": prediction.get("paeImageUrl"),
                    "pae_doc_url": prediction.get("paeDocUrl"),
                    "structure_url": prediction.get("pdbUrl"),
                    "note": "Download the .pdb or .cif file to extract per-residue pLDDT scores from the B-factor column."
                }))
        except urllib.error.HTTPError as e:
            return ToolResult(success=False, error=f"AlphaFold API error: {e.code} - {e.reason}")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to fetch confidence: {str(e)}")


class AlphaFoldSkill(BaseSkill):
    """
    Skill for interacting with the AlphaFold Protein Structure Database API.
    Fetches structure metadata and analyzes confidence (pLDDT) scores.
    """
    
    @property
    def skill_id(self) -> str:
        return "alphafold"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Fetch and analyze protein structures from the AlphaFold DB."

    def get_tools(self) -> List[BaseTool]:
        return [
            FetchStructureTool(),
            AnalyzeConfidenceTool()
        ]
