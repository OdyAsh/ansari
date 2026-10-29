import {McpServer} from '@modelcontextprotocol/server';
import {attachRequestLifetime, createServerBudgets, registerPublicOperation, type OperationView} from '@saga-a2ui/react-mcp-stack/server';
import {z} from 'zod';
import type {AnswerCompleter} from './ansari-client.js';
import {writeOperationLog,type OperationLogger} from './operation-log.js';
import {answerFingerprint} from './answer-fingerprint.js';
import {APP_VERSION} from '../version.js';
import {ANSWER_ENVELOPE_VERSION} from '../view/envelope.js';


// Existing application identity and text, not a new release declaration.
export const SERVER_NAME='ansari-mcp';
export const TOOL_UNAVAILABLE_TEXT='Ansari could not complete this request.';
/** The handler wait the tool declares; the request budget of each call below is the same, never the SDK default. */
export const HANDLER_BOUND_MS=60_000;
export type CreateMcpServerOptions = {
  completer: AnswerCompleter;
  view: OperationView;
  logger: OperationLogger;
  release?: string;
  requestMs?: number;
};

export function createAnsariMcpServer(options:CreateMcpServerOptions):McpServer{
  // Without a context the SDK default 10 s request budget would clamp the declared handler bound below. The request
  // budget is the declared bound itself: no new timeout target and no change to the SDK defaults. Each call gets its own.
  const budgets=createServerBudgets({requestMs:options.requestMs??HANDLER_BOUND_MS});
  const lifetime=budgets.startRequest();
  const server=new McpServer({name:SERVER_NAME,version:APP_VERSION},{capabilities:{tools:{},resources:{}}});
  // The request's timer ends when this server does: on its transport closing, and on close() with or without a transport
  // (the handler also disposes a server it never connected). The SDK owns that rule; the application subclasses nothing.
  attachRequestLifetime(server,lifetime);
  registerPublicOperation(server,{
    name:'answer_islamic_question',
    title:'Answer Islamic Question',
    description:'Answers questions about Islam including theology, jurisprudence (fiqh), history, Quranic interpretation (tafsir), Hadith sciences, Islamic law, worship practices, ethics, spirituality, and contemporary issues. Provides comprehensive answers with citations from Quran, authentic Hadith, and classical Islamic scholarship. Can handle questions about specific verses, hadiths, scholarly opinions, historical events, prophets, companions, and modern applications of Islamic principles.',
    // Preserve the legacy discovery contract; read-only tools need no destructive/idempotent hints.
    annotations:{readOnlyHint:true,openWorldHint:true},
    // Application handler bound, with a fresh lifetime per call.
    boundMs:HANDLER_BOUND_MS,
    input:z.object({question:z.string().min(1).max(4000).describe('The question about Islam to be answered. Include as much context and detail as possible. You can ask about specific Quranic verses, hadiths, Islamic rulings, historical events, theological concepts, or practical applications. The more context provided, the more comprehensive and relevant the answer will be.')}),
    output:z.object({version:z.literal(ANSWER_ENVELOPE_VERSION),question:z.string(),answer:z.string()}).strict(),
    // Discovery, every result's _meta.ui.resourceUri and the resource read all come from this one declaration.
    view:options.view,
    failures:{absent:TOOL_UNAVAILABLE_TEXT,unavailable:TOOL_UNAVAILABLE_TEXT,invalidOutput:TOOL_UNAVAILABLE_TEXT},
    async handle({question},request){
      writeOperationLog({event:'source.start',method:'tools/call'},options.logger,options.release);
      await request.reportProgress({progress:0,total:1});
      // Source records do not claim terminal MCP delivery or upstream abortion.
      let answer:string;
      try {
        answer=await options.completer.complete({question,signal:request.signal});
      } catch (error) {
        // Record source failure without inspecting or serializing the exception.
        writeOperationLog({event:'source.error',method:'tools/call'},options.logger,options.release);
        throw error;
      }
      writeOperationLog({event:'source.complete',method:'tools/call',...answerFingerprint(answer)},options.logger,options.release);
      return {kind:'record' as const,record:{question,answer}};
    },
    project:record=>({text:record.answer,data:{version:ANSWER_ENVELOPE_VERSION,question:record.question,answer:record.answer}}),
  },{lifetime});
  return server;
}
