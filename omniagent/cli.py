import argparse
import sys
import uvicorn
import asyncio
from omniagent.ui.server import app

def main():
    parser = argparse.ArgumentParser(description="OMNI-CLAW CLI")
    subparsers = parser.add_subparsers(dest="command")
    
    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the Visual Workflow Builder UI")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    
    # run-workflow
    run_parser = subparsers.add_parser("run-workflow", help="Run a workflow")
    run_parser.add_argument("path", help="Path to workflow file")
    
    # list-skills
    list_parser = subparsers.add_parser("list-skills", help="List available skills")
    
    args = parser.parse_args()
    
    if args.command == "serve":
        print(f"Starting OMNI-CLAW Visual Workflow Builder UI on http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.command == "run-workflow":
        print(f"Running workflow from {args.path}... (Not fully wired to runner yet)")
    elif args.command == "list-skills":
        from omniagent.skills.base import SkillRegistry
        print("Registered Skills:")
        for skill_id, version in SkillRegistry._registry.items():
            print(f"- {skill_id} (v{version})")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
