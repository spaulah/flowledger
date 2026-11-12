from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv
from llm_clients import build_chat_model
from redaction import redact

load_dotenv()

class AIAnalysisService:
    def __init__(self, rag_service=None):
        self.llm = build_chat_model()

        # Initialize simple chat memory (list of message tuples)
        self.chat_history = []

        self.rag_chain = None
        self._answer_chain = None
        self.rag_service = None
        if rag_service:
            self._setup_rag_chain(rag_service)

    def _setup_rag_chain(self, rag_service):
        # keep reference for retrieval metadata
        self.rag_service = rag_service
        # Create RAG chain using LCEL (LangChain Expression Language)
        template = """
You are an expert Power Automate Solution Architect. You have access to definitions and run histories of multiple flows.
Analyze logic, health patterns, and relationships across flows to answer the user query.
Focus on cross-flow dependencies, triggers, and reliability.
Keep the tone professional and focused on actionable insights.

Context:
{context}

Question: {question}

Answer in Markdown format:"""

        prompt = ChatPromptTemplate.from_template(template)

        def format_docs(docs):
            # Redact again before the text reaches the chat model, so indexes built before redaction existed are covered too.
            return redact("\n\n".join(doc.page_content for doc in docs))

        self.rag_chain = (
            {"context": rag_service.get_retriever() | format_docs, "question": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )
        # Same prompt, but the caller supplies the context. Used by ask_sync, which retrieves once itself.
        self._answer_chain = prompt | self.llm | StrOutputParser()

    def ask(self, question: str) -> str:
        """
        Ask a question using the conversational RAG chain.
        """
        if not self.rag_chain:
            raise ValueError("RAG chain not initialized. Please provide a rag_service to the constructor.")

        # Get chat history (last 6 messages for context)
        history_str = ""
        if self.chat_history:
            recent_history = self.chat_history[-6:]  # Last 3 exchanges
            history_lines = []
            for msg_type, content in recent_history:
                history_lines.append(f"{msg_type}: {content}")
            history_str = "\n".join(history_lines)

        # Create enhanced question with history
        enhanced_question = f"Chat History:\n{history_str}\n\nCurrent Question: {question}" if history_str else question

        # Invoke the chain
        response = self.rag_chain.invoke(enhanced_question)

        # Save to memory
        self.chat_history.append(("user", question))
        self.chat_history.append(("assistant", response))

        return response

    def ask_sync(self, question: str, k: int = 8):
        """Answer one question. Used by the HTTP API.

        Stateless and safe to call from several threads: it keeps no chat history. It retrieves once,
        so the returned sources are exactly the context the model saw. A retrieval or model failure
        raises, instead of answering without context.

        Returns a tuple of (response_text, sources). Each source is a dict with `metadata` and `snippet`.
        """
        if not self._answer_chain or not self.rag_service:
            raise ValueError("RAG chain not initialized. Please provide a rag_service to the constructor.")

        docs = self.rag_service.get_retriever(k=k).invoke(question)
        context = redact("\n\n".join(d.page_content for d in docs))
        response = self._answer_chain.invoke({"context": context, "question": question})

        sources = [
            {"metadata": d.metadata or {}, "snippet": redact(d.page_content[:800])}
            for d in docs
        ]
        return response, sources