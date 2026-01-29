import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useState, useRef, useEffect } from 'react';
import { ArrowLeft, Send, Loader2, Bot, User, ChevronDown, ChevronRight, Wrench, Paperclip, X, Music, FileText, AlertTriangle, Check, Square } from 'lucide-react';
import type { MessageContent, UniversalContent, ApprovalRequiredEvent } from '../types';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Utility: Convert File to base64
const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = error => reject(error);
  });
};

type Attachment = {
  id: string;
  file: File;
  preview?: string;
  type: 'image' | 'audio' | 'file';
};

export function ChatDetail() {
  const { chatId } = useParams<{ chatId: string }>();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const streamReaderRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const [expandedToolCalls, setExpandedToolCalls] = useState<Set<string>>(new Set());
  const [streamingContent, setStreamingContent] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequiredEvent[]>([]);
  const [processingApproval, setProcessingApproval] = useState<string | null>(null);

  const toggleToolCall = (messageId: string) => {
    setExpandedToolCalls(prev => {
      const newSet = new Set(prev);
      if (newSet.has(messageId)) {
        newSet.delete(messageId);
      } else {
        newSet.add(messageId);
      }
      return newSet;
    });
  };

  const { data: chat, isLoading } = useQuery({
    queryKey: ['chat', chatId],
    queryFn: () => apiClient.getChat(chatId!),
    enabled: !!chatId,
    // No polling needed with streaming
  });

  const handleApproval = async (approval: ApprovalRequiredEvent, approved: boolean) => {
    if (!chatId) return;

    setProcessingApproval(approval.tool_call_id);

    try {
      const token = localStorage.getItem('auth_token');
      const baseUrl = window.location.hostname === 'localhost'
        ? 'http://localhost:8000'
        : `${window.location.protocol}//${window.location.hostname}`;

      const response = await fetch(`${baseUrl}/chats/${chatId}/approve-tool/${approval.tool_call_id}`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ approved }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // Remove from pending approvals
      setPendingApprovals(prev => prev.filter(a => a.tool_call_id !== approval.tool_call_id));

      // Refresh chat to show execution results
      queryClient.invalidateQueries({ queryKey: ['chat', chatId] });

    } catch (error) {
      console.error('Approval error:', error);
      alert('Failed to process approval. Please try again.');
    } finally {
      setProcessingApproval(null);
    }
  };

  const sendStreamingMessage = async (content: MessageContent) => {
    if (!chatId) return;

    setIsStreaming(true);
    setStreamingContent('');

    // Optimistically add user message
    queryClient.setQueryData(['chat', chatId], (old: any) => {
      if (!old) return old;
      return {
        ...old,
        messages: [
          ...old.messages,
          {
            id: 'temp-user-' + Date.now(),
            chat_id: chatId,
            role: 'user',
            content: content,
            tool_calls: null,
            tool_call_id: null,
            name: null,
            created_at: new Date().toISOString(),
          },
        ],
      };
    });

    const token = localStorage.getItem('auth_token');
    const baseUrl = window.location.hostname === 'localhost'
      ? 'http://localhost:8000'
      : `${window.location.protocol}//${window.location.hostname}`;

    try {
      // Make POST request to streaming endpoint (Runtime API)
      const response = await fetch(`${baseUrl}/chats/${chatId}/messages/stream`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content: content,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      // Read the SSE stream
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      // Store reader ref for cancellation
      streamReaderRef.current = reader;

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // Decode chunk and add to buffer
        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE messages (format: "event: message\ndata: {...}\n\n")
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        let eventType = '';
        for (const line of lines) {
          if (line.startsWith('event:')) {
            eventType = line.substring(6).trim();
          } else if (line.startsWith('data:')) {
            const data = line.substring(5).trim();
            if (!data) continue;

            try {
              const parsed = JSON.parse(data);

              if (eventType === 'message') {
                // Append content chunk
                if (parsed.content) {
                  setStreamingContent(prev => prev + parsed.content);
                }

                // Handle approval_required events
                if (parsed.type === 'approval_required') {
                  setPendingApprovals(prev => [...prev, parsed]);
                }
              } else if (eventType === 'done') {
                // Stream complete
                setIsStreaming(false);
                setStreamingContent('');
                streamReaderRef.current = null;
                queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
                return;
              } else if (eventType === 'error') {
                console.error('Streaming error:', parsed.error);
                setIsStreaming(false);
                setStreamingContent('');
                streamReaderRef.current = null;
                return;
              }
            } catch (e) {
              console.error('Failed to parse SSE data:', data, e);
            }
          }
        }
      }

      // Stream ended normally
      setIsStreaming(false);
      setStreamingContent('');
      streamReaderRef.current = null;
      queryClient.invalidateQueries({ queryKey: ['chat', chatId] });

    } catch (error) {
      console.error('Streaming error:', error);
      setIsStreaming(false);
      setStreamingContent('');
      streamReaderRef.current = null;
      // Refetch to get any partial results
      queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
    }
  };

  const handleStop = () => {
    if (streamReaderRef.current) {
      streamReaderRef.current.cancel();
      streamReaderRef.current = null;
      setIsStreaming(false);
      setStreamingContent('');
      console.log('🛑 Stream manually stopped by user');

      // Refetch chat to show partial message saved by backend
      queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat?.messages, streamingContent]);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);

    for (const file of files) {
      const id = Math.random().toString(36);
      let type: 'image' | 'audio' | 'file' = 'file';
      let preview: string | undefined;

      if (file.type.startsWith('image/')) {
        type = 'image';
        preview = await fileToBase64(file);
      } else if (file.type.startsWith('audio/')) {
        type = 'audio';
      }

      setAttachments(prev => [...prev, { id, file, preview, type }]);
    }

    // Reset file input
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id));
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!message.trim() && attachments.length === 0) || isStreaming) return;

    // Build multimodal content
    let content: MessageContent;

    if (attachments.length > 0) {
      const contentParts: UniversalContent[] = [];

      // Add text if present
      if (message.trim()) {
        contentParts.push({
          type: 'text',
          text: message.trim(),
        });
      }

      // Add attachments
      for (const attachment of attachments) {
        const base64 = await fileToBase64(attachment.file);

        if (attachment.type === 'image') {
          contentParts.push({
            type: 'image',
            image: base64,
          });
        } else if (attachment.type === 'audio') {
          const format = attachment.file.name.split('.').pop() as 'wav' | 'mp3' | 'm4a' | 'ogg';
          contentParts.push({
            type: 'audio',
            data: base64.split(',')[1], // Remove data URL prefix
            format: format || 'mp3',
          });
        } else {
          contentParts.push({
            type: 'file',
            file_data: base64.split(',')[1],
            filename: attachment.file.name,
            mime_type: attachment.file.type,
          });
        }
      }

      content = contentParts;
    } else {
      content = message.trim();
    }

    sendStreamingMessage(content);
    setMessage('');
    setAttachments([]);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  if (!chat) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold text-gray-900">Chat not found</h2>
        <Link to="/chats" className="text-primary-600 hover:text-primary-700 mt-2 inline-block">
          Back to chats
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-gray-200">
        <div className="flex items-center">
          <Link to="/chats" className="mr-4 text-gray-600 hover:text-gray-900">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{chat.title}</h1>
            <p className="text-sm text-gray-500">
              {chat.messages.length} messages • Created {new Date(chat.created_at).toLocaleDateString()}
            </p>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto py-4 space-y-4">
        {chat.messages.length === 0 ? (
          <div className="text-center py-12">
            <Bot className="w-16 h-16 text-gray-400 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">Start a conversation</h3>
            <p className="text-gray-600">Send a message to begin chatting with your AI assistant</p>
          </div>
        ) : (
          chat.messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`flex items-start max-w-[80%] ${
                  msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'
                }`}
              >
                <div
                  className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
                    msg.role === 'user'
                      ? 'bg-primary-600 text-white ml-3'
                      : 'bg-gray-200 text-gray-600 mr-3'
                  }`}
                >
                  {msg.role === 'user' ? (
                    <User className="w-5 h-5" />
                  ) : (
                    <Bot className="w-5 h-5" />
                  )}
                </div>
                <div className="flex-1">
                  {/* Tool response message */}
                  {msg.role === 'tool' ? (
                    <div className="border border-purple-200 bg-purple-50 rounded-lg overflow-hidden">
                      <button
                        onClick={() => toggleToolCall(msg.id)}
                        className="w-full px-3 py-2 flex items-center justify-between hover:bg-purple-100 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          <Wrench className="w-4 h-4 text-purple-600" />
                          <span className="text-sm font-medium text-purple-900">
                            Tool Response: {msg.name}
                          </span>
                        </div>
                        {expandedToolCalls.has(msg.id) ? (
                          <ChevronDown className="w-4 h-4 text-purple-600" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-purple-600" />
                        )}
                      </button>
                      {expandedToolCalls.has(msg.id) && (
                        <div className="px-3 py-2 border-t border-purple-200 bg-white">
                          <pre className="text-xs text-gray-800 whitespace-pre-wrap font-mono overflow-x-auto">
                            {typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  ) : (
                    /* Regular user/assistant message */
                    <div
                      className={`rounded-lg px-4 py-2 ${
                        msg.role === 'user'
                          ? 'text-white'
                          : 'text-gray-900'
                      }`}
                      style={{
                        backgroundColor: msg.role === 'user' ? '#2563eb' : '#f3f4f6'
                      }}
                    >
                      {/* Render multimodal content */}
                      {(() => {
                        // Parse content if it's a JSON string
                        let content = msg.content;
                        if (typeof content === 'string' && content.trim().startsWith('[')) {
                          try {
                            content = JSON.parse(content);
                          } catch (e) {
                            // Not valid JSON, keep as string
                          }
                        }

                        if (Array.isArray(content)) {
                          return (
                            <div className="space-y-2">
                              {content.map((part, idx) => {
                                if (part.type === 'text') {
                                  return msg.role === 'assistant' ? (
                                    <div key={idx} className="text-sm prose prose-sm max-w-none prose-pre:bg-gray-800 prose-pre:text-gray-100">
                                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                        {part.text}
                                      </ReactMarkdown>
                                    </div>
                                  ) : (
                                    <p key={idx} className="text-sm whitespace-pre-wrap">{part.text}</p>
                                  );
                                } else if (part.type === 'image') {
                                  return (
                                    <img
                                      key={idx}
                                      src={part.image}
                                      alt="Attached image"
                                      className="max-w-sm rounded-lg"
                                    />
                                  );
                                } else if (part.type === 'audio') {
                                  return (
                                    <div key={idx} className="flex items-center gap-2 p-2 bg-white bg-opacity-20 rounded">
                                      <Music className="w-4 h-4" />
                                      <span className="text-xs">Audio file ({part.format})</span>
                                    </div>
                                  );
                                } else if (part.type === 'file') {
                                  return (
                                    <div key={idx} className="flex items-center gap-2 p-2 bg-white bg-opacity-20 rounded">
                                      <FileText className="w-4 h-4" />
                                      <span className="text-xs">{part.filename || 'File attachment'}</span>
                                    </div>
                                  );
                                }
                                return null;
                              })}
                            </div>
                          );
                        } else {
                          // Regular string content
                          return msg.role === 'assistant' ? (
                            <div className="text-sm prose prose-sm max-w-none prose-pre:bg-gray-800 prose-pre:text-gray-100">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {content || ''}
                              </ReactMarkdown>
                            </div>
                          ) : (
                            <p className="text-sm whitespace-pre-wrap">{content}</p>
                          );
                        }
                      })()}
                    </div>
                  )}

                  {/* Tool calls made by assistant */}
                  {msg.tool_calls && msg.tool_calls.length > 0 && (
                    <div className="mt-2 border border-amber-200 bg-amber-50 rounded-lg overflow-hidden">
                      <button
                        onClick={() => toggleToolCall(`${msg.id}-calls`)}
                        className="w-full px-3 py-2 flex items-center justify-between hover:bg-amber-100 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          <Wrench className="w-4 h-4 text-amber-600" />
                          <span className="text-sm font-medium text-amber-900">
                            {msg.tool_calls.length} Tool Call{msg.tool_calls.length > 1 ? 's' : ''}
                          </span>
                        </div>
                        {expandedToolCalls.has(`${msg.id}-calls`) ? (
                          <ChevronDown className="w-4 h-4 text-amber-600" />
                        ) : (
                          <ChevronRight className="w-4 h-4 text-amber-600" />
                        )}
                      </button>
                      {expandedToolCalls.has(`${msg.id}-calls`) && (
                        <div className="px-3 py-2 border-t border-amber-200 bg-white space-y-2">
                          {msg.tool_calls.map((call: any, idx: number) => (
                            <div key={idx} className="text-xs">
                              <p className="font-medium text-amber-900">
                                {call.function?.name || 'Unknown function'}
                              </p>
                              {call.function?.arguments && (
                                <pre className="mt-1 text-gray-700 whitespace-pre-wrap font-mono overflow-x-auto">
                                  {call.function.arguments}
                                </pre>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))
        )}

        {/* Streaming message */}
        {isStreaming && streamingContent && (
          <div className="flex justify-start">
            <div className="flex items-start max-w-[80%]">
              <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-gray-200 text-gray-600 mr-3">
                <Bot className="w-5 h-5" />
              </div>
              <div className="flex-1">
                <div className="rounded-lg px-4 py-2 bg-gray-100 text-gray-900">
                  <div className="flex items-start gap-2">
                    <div className="text-sm prose prose-sm max-w-none prose-pre:bg-gray-800 prose-pre:text-gray-100 flex-1">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {streamingContent}
                      </ReactMarkdown>
                    </div>
                    <span className="inline-block w-2 h-4 bg-primary-600 animate-pulse flex-shrink-0 mt-1"></span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Pending Approvals */}
        {pendingApprovals.length > 0 && (
          <div className="space-y-3">
            {pendingApprovals.map((approval) => {
              const isProcessing = processingApproval === approval.tool_call_id;
              return (
                <div key={approval.tool_call_id} className={`border-2 ${isProcessing ? 'border-blue-400 bg-blue-50' : 'border-yellow-400 bg-yellow-50'} rounded-lg p-4 shadow-md transition-all`}>
                  <div className="flex items-start gap-3">
                    <div className={`flex-shrink-0 w-10 h-10 rounded-full ${isProcessing ? 'bg-blue-100' : 'bg-yellow-100'} flex items-center justify-center`}>
                      {isProcessing ? (
                        <Loader2 className="w-6 h-6 text-blue-600 animate-spin" />
                      ) : (
                        <AlertTriangle className="w-6 h-6 text-yellow-600" />
                      )}
                    </div>
                    <div className="flex-1">
                      <h4 className={`font-semibold ${isProcessing ? 'text-blue-900' : 'text-yellow-900'} mb-1`}>
                        {isProcessing ? 'Processing...' : 'Function Approval Required'}
                      </h4>
                      <p className={`text-sm ${isProcessing ? 'text-blue-800' : 'text-yellow-800'} mb-2`}>
                        The agent wants to call <code className={`px-2 py-0.5 ${isProcessing ? 'bg-blue-200' : 'bg-yellow-200'} rounded font-mono text-xs`}>{approval.function_namespace}/{approval.function_name}</code>
                      </p>

                      {/* Show arguments */}
                      {Object.keys(approval.arguments).length > 0 && (
                        <div className="mb-3">
                          <p className={`text-xs font-medium ${isProcessing ? 'text-blue-900' : 'text-yellow-900'} mb-1`}>Arguments:</p>
                          <pre className={`text-xs bg-white border ${isProcessing ? 'border-blue-200' : 'border-yellow-200'} rounded p-2 overflow-x-auto`}>
                            {JSON.stringify(approval.arguments, null, 2)}
                          </pre>
                        </div>
                      )}

                      <div className="flex gap-2">
                        <button
                          onClick={() => handleApproval(approval, true)}
                          disabled={isProcessing}
                          className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white rounded-lg font-medium text-sm transition-colors"
                        >
                          {isProcessing ? (
                            <>
                              <Loader2 className="w-4 h-4 animate-spin" />
                              Processing...
                            </>
                          ) : (
                            <>
                              <Check className="w-4 h-4" />
                              Approve
                            </>
                          )}
                        </button>
                        <button
                          onClick={() => handleApproval(approval, false)}
                          disabled={isProcessing}
                          className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-400 disabled:cursor-not-allowed text-white rounded-lg font-medium text-sm transition-colors"
                        >
                          <X className="w-4 h-4" />
                          Reject
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="pt-4 border-t border-gray-200">
        {/* Attachments Preview */}
        {attachments.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {attachments.map((attachment) => (
              <div key={attachment.id} className="relative group">
                <div className="flex items-center gap-2 px-3 py-2 bg-gray-100 rounded-lg border border-gray-200">
                  {attachment.type === 'image' && attachment.preview ? (
                    <img src={attachment.preview} alt={attachment.file.name} className="w-12 h-12 object-cover rounded" />
                  ) : attachment.type === 'audio' ? (
                    <Music className="w-5 h-5 text-purple-600" />
                  ) : (
                    <FileText className="w-5 h-5 text-blue-600" />
                  )}
                  <span className="text-sm text-gray-700 max-w-[150px] truncate">
                    {attachment.file.name}
                  </span>
                  <button
                    onClick={() => removeAttachment(attachment.id)}
                    className="ml-1 p-1 hover:bg-gray-200 rounded transition-colors"
                    type="button"
                  >
                    <X className="w-4 h-4 text-gray-500" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        <form onSubmit={handleSend} className="flex items-start gap-2">
          <div className="flex-1">
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(e);
                }
              }}
              placeholder="Type your message... (Shift+Enter for new line)"
              rows={3}
              className="w-full input resize-none"
              disabled={isStreaming}
            />
          </div>
          <div className="flex flex-col gap-2">
            <input
              ref={fileInputRef}
              type="file"
              onChange={handleFileSelect}
              className="hidden"
              multiple
              accept="image/*,audio/*,.pdf,.doc,.docx,.txt"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isStreaming}
              className="btn btn-secondary px-4 py-2 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Attach files"
            >
              <Paperclip className="w-5 h-5" />
            </button>
            {isStreaming ? (
              <button
                type="button"
                onClick={handleStop}
                className="btn px-4 py-2 bg-red-600 hover:bg-red-700 text-white"
                title="Stop generation"
              >
                <Square className="w-5 h-5" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!message.trim() && attachments.length === 0}
                className="btn btn-primary px-4 py-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Send className="w-5 h-5" />
              </button>
            )}
          </div>
        </form>
        <p className="text-xs text-gray-500 mt-2">
          Press Enter to send, Shift+Enter for new line • Click <Paperclip className="w-3 h-3 inline" /> to attach images, audio, or documents
        </p>
      </div>
    </div>
  );
}
