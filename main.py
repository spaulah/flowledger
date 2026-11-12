"""Command-line interface for flow analysis.

Downloads (optionally) flow definitions and run history, indexes them into the
local vector store, and opens an interactive question loop.

The HTTP API lives in api.py (run with `uvicorn api:app`).
"""
import argparse
import logging

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from ai_analysis_service import AIAnalysisService
from flow_data import load_flow_data
from jobs.download_flows import main as download_all_flows
from logging_config import configure_logging
from rag_service import RAGService, chroma_path

configure_logging()


def main(skip_download=False):
    try:
        if not skip_download:
            logging.info("Downloading flow definitions and run history...")
            download_all_flows()
        else:
            logging.info("Skipping download (--skip-download).")

        logging.info("Loading flow data for analysis...")
        flows_data = load_flow_data()
        logging.info(f"Loaded data for {len(flows_data)} flows.")

        logging.info("Initializing RAG knowledge base...")
        # Same persisted index as jobs/reindex_local.py and api.py. Documents already in it are not embedded again.
        rag_service = RAGService(persist_directory=chroma_path())
        rag_service.index_flows(flows_data)

        ai_service = AIAnalysisService(rag_service=rag_service)
        console = Console()
        console.print(Panel.fit(
            "[bold cyan]FlowLedger[/bold cyan]\n"
            "[white]Ask questions about your flows and their run history.[/white]\n"
            "[dim]Examples: 'Which flows update SharePoint?', 'Find potential loops', "
            "'Summarize the approval flow logic'[/dim]\n\n"
            "[bold red]exit[/bold red] or [bold red]quit[/bold red] to end the session",
            title="[bold green]AI Assistant[/bold green]",
            border_style="bright_blue",
        ))

        while True:
            try:
                user_prompt = Prompt.ask("\n[bold yellow]Analysis Query[/bold yellow]")

                if user_prompt.lower() in ['exit', 'quit']:
                    console.print("[bold red]Exiting. Goodbye![/bold red]")
                    break

                if not user_prompt:
                    continue

                with console.status("[bold green]Querying knowledge base and analyzing...", spinner="dots"):
                    response = ai_service.ask(user_prompt)

                console.print(Rule("Analysis Result", style="cyan"))
                console.print(Markdown(response))
                console.print(Rule(style="cyan"))

            except KeyboardInterrupt:
                console.print("\n[bold red]Exiting.[/bold red]")
                break
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
                logging.error(f"Error during interactive analysis: {e}")

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        print("Check your .env file: LLM_BASE_URL/LLM_API_KEY and, for downloads, M365_CLIENT_ID/M365_CLIENT_SECRET/M365_TENANT_ID.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Power Automate flow analysis CLI')
    parser.add_argument('--skip-download', action='store_true', default=False,
                        help='Use flow data already on disk instead of downloading')
    args = parser.parse_args()
    main(skip_download=args.skip_download)
