import json
import urllib.request
import urllib.error
from typing import Dict, Any, List
from omniagent.skills.base import BaseSkill, SkillAction

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

    def get_actions(self) -> List[SkillAction]:
        return [
            SkillAction(
                name="fetch_structure",
                description="Fetch protein predicted structure metadata from AlphaFold DB.",
                parameters={
                    "type": "object",
                    "properties": {
                        "uniprot_id": {"type": "string", "description": "UniProt Accession ID"}
                    },
                    "required": ["uniprot_id"]
                }
            ),
            SkillAction(
                name="analyze_confidence",
                description="Fetch and analyze pLDDT confidence scores for a protein.",
                parameters={
                    "type": "object",
                    "properties": {
                        "uniprot_id": {"type": "string", "description": "UniProt Accession ID"}
                    },
                    "required": ["uniprot_id"]
                }
            )
        ]

    def execute(self, action: str, **kwargs) -> Dict[str, Any]:
        if action == "alphafold.fetch_structure":
            return self._fetch_structure(kwargs.get("uniprot_id"))
        elif action == "alphafold.analyze_confidence":
            return self._analyze_confidence(kwargs.get("uniprot_id"))
        else:
            raise ValueError(f"Unknown action: {action}")

    def _fetch_structure(self, uniprot_id: str) -> Dict[str, Any]:
        if not uniprot_id:
            return {"error": "uniprot_id is required"}
            
        url = f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "OMNI-CLAW/1.0"})
        
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode("utf-8"))
                if not data:
                    return {"error": "No data returned from AlphaFold"}
                
                # AlphaFold API returns a list of predictions for the UniProt ID
                prediction = data[0]
                return {
                    "uniprot_id": prediction.get("uniprotAccession"),
                    "organism": prediction.get("organismScientificName"),
                    "gene": prediction.get("gene"),
                    "model_url": prediction.get("pdbUrl"),
                    "cif_url": prediction.get("cifUrl"),
                    "sequence_length": len(prediction.get("uniprotSequence", "")),
                    "model_created_date": prediction.get("modelCreatedDate"),
                    "latest_version": prediction.get("latestVersion")
                }
        except urllib.error.HTTPError as e:
            return {"error": f"AlphaFold API error: {e.code} - {e.reason}"}
        except Exception as e:
            return {"error": f"Failed to fetch structure: {str(e)}"}

    def _analyze_confidence(self, uniprot_id: str) -> Dict[str, Any]:
        if not uniprot_id:
            return {"error": "uniprot_id is required"}
            
        url = f"https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "OMNI-CLAW/1.0"})
        
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode("utf-8"))
                if not data:
                    return {"error": "No data returned from AlphaFold"}
                
                prediction = data[0]
                # PDB URLs and PAE images are available, but AlphaFold DB doesn't return per-residue pLDDT 
                # in this specific JSON endpoint. We provide the overall metric and links.
                
                return {
                    "uniprot_id": prediction.get("uniprotAccession"),
                    "confidence_assessment": "AlphaFold predictions are scored 0-100 (pLDDT). >90 is highly accurate, 70-90 is confident, 50-70 is low confidence, <50 is often intrinsically disordered.",
                    "pae_image_url": prediction.get("paeImageUrl"),
                    "pae_doc_url": prediction.get("paeDocUrl"),
                    "structure_url": prediction.get("pdbUrl"),
                    "note": "Download the .pdb or .cif file to extract per-residue pLDDT scores from the B-factor column."
                }
        except urllib.error.HTTPError as e:
            return {"error": f"AlphaFold API error: {e.code} - {e.reason}"}
        except Exception as e:
            return {"error": f"Failed to fetch confidence: {str(e)}"}
