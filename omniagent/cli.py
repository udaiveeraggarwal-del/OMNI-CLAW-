import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description="OMNI-CLAW CLI")
    subparsers = parser.add_subparsers(dest="command")
    
    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the Visual Workflow Builder UI")
    
    # run-workflow
    run_parser = subparsers.add_parser("run-workflow", help="Run a workflow")
    run_parser.add_argument("path", help="Path to workflow file")
    
    # list-skills
    list_parser = subparsers.add_parser("list-skills", help="List available skills")
    
    args = parser.parse_args()
    
    if args.command == "serve":
        print("Starting UI...")
    elif args.command == "run-workflow":
        print(f"Running workflow from {args.path}...")
    elif args.command == "list-skills":
        print("Listing skills...")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
