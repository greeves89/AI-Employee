#!/usr/bin/env node

/**
 * Bash Approval MCP Server
 * 
 * Intercepts bash commands and enforces approval workflow:
 * 1. Analyzes command risk using DB-backed command policies
 * 2. Blocks forbidden commands immediately
 * 3. Requests approval for risky commands from orchestrator
 * 4. Polls for user decision
 * 5. Executes or rejects based on approval
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { exec } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || "http://orchestrator:8000";
const AGENT_TOKEN = process.env.AGENT_TOKEN || "";
const AGENT_ID = process.env.AGENT_ID || "";
const COMMAND_POLICY_CACHE_TTL_MS = 10000;

let commandPolicyCache = { expiresAt: 0, policies: [] };

const server = new Server(
  {
    name: "bash-approval",
    version: "1.0.0",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

/**
 * Analyze command risk using DB-backed command policies.
 */
async function analyzeCommand(command) {
  try {
    const policies = await fetchCommandPolicies();
    for (const policy of policies) {
      const pattern = policy.pattern || "";
      if (!pattern) continue;
      try {
        if (new RegExp(pattern, "im").test(command)) {
          const effect = ["blocked", "high", "medium", "allow"].includes(policy.effect)
            ? policy.effect
            : "blocked";
          if (effect === "allow") {
            return { risk_level: "low", reason: policy.description || policy.name || pattern };
          }
          return { risk_level: effect, reason: policy.description || policy.name || pattern };
        }
      } catch (error) {
        console.error(`Invalid command policy regex "${pattern}":`, error);
      }
    }
    return { risk_level: "low", reason: "No command policy matched" };
  } catch (error) {
    console.error("Command analysis failed:", error);
    return { risk_level: "low", reason: "Policy lookup failed - allowing command" };
  }
}

async function fetchCommandPolicies() {
  const now = Date.now();
  if (commandPolicyCache.expiresAt > now) {
    return commandPolicyCache.policies;
  }
  const response = await fetch(`${ORCHESTRATOR_URL}/api/v1/command-policies/for-agent/${AGENT_ID}`, {
    headers: {
      "Authorization": `Bearer ${AGENT_TOKEN}`,
      "X-Agent-ID": AGENT_ID,
    },
  });
  if (!response.ok) {
    throw new Error(`Policy fetch failed: HTTP ${response.status}`);
  }
  const data = await response.json();
  commandPolicyCache = {
    expiresAt: now + COMMAND_POLICY_CACHE_TTL_MS,
    policies: Array.isArray(data.policies) ? data.policies : [],
  };
  return commandPolicyCache.policies;
}

/**
 * Request approval from orchestrator
 */
async function requestApproval(command, reasoning, risk_level) {
  const response = await fetch(`${ORCHESTRATOR_URL}/api/v1/approvals/request`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${AGENT_TOKEN}`,
      "X-Agent-ID": AGENT_ID,
    },
    body: JSON.stringify({
      tool: "bash",
      input: { command },
      reasoning,
      risk_level,
      target_channel: "all",
    }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Approval request failed: ${error}`);
  }

  return await response.json();
}

/**
 * Poll for approval decision
 */
async function waitForApproval(approvalId, maxWaitSeconds = 300) {
  const startTime = Date.now();
  
  while (true) {
    const response = await fetch(`${ORCHESTRATOR_URL}/api/v1/approvals/check/${approvalId}`, {
      headers: {
        "Authorization": `Bearer ${AGENT_TOKEN}`,
        "X-Agent-ID": AGENT_ID,
      },
    });

    if (!response.ok) {
      throw new Error("Failed to check approval status");
    }

    const status = await response.json();
    
    if (status.status === "approved") {
      return { approved: true };
    }
    
    if (status.status === "denied") {
      return { approved: false, reason: status.user_response || status.reason };
    }

    // Check timeout
    if (Date.now() - startTime > maxWaitSeconds * 1000) {
      throw new Error("Approval request timed out");
    }

    // Wait 2 seconds before next poll
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

/**
 * Execute bash command with approval workflow
 */
async function executeBashWithApproval(command, description, timeout) {
  // Analyze command risk
  const { risk_level, reason } = await analyzeCommand(command);

  // Blocked commands cannot be executed
  if (risk_level === "blocked") {
    throw new Error(`🚫 BLOCKED: ${reason}\n\nThis command is forbidden and cannot be executed.`);
  }

  // Low-risk commands can execute immediately
  if (risk_level === "low") {
    const { stdout, stderr } = await execAsync(command, {
      timeout: timeout || 120000,
      maxBuffer: 30 * 1024 * 1024, // 30MB
    });
    return stdout + stderr;
  }

  // Medium/High risk commands require approval
  console.error(`⚠️ Command requires approval (${risk_level} risk): ${reason}`);
  console.error(`Requesting user approval...`);

  const approvalRequest = await requestApproval(command, reason, risk_level);
  const approvalId = approvalRequest.approval_id;

  console.error(`Approval request created: ${approvalId}`);
  console.error(`Waiting for user decision...`);

  const decision = await waitForApproval(approvalId);

  if (!decision.approved) {
    throw new Error(`❌ Command denied by user.\n\n[EXTERNAL-DATA source="denial-reason"]\n${decision.reason || "No reason provided"}\n[/EXTERNAL-DATA]`);
  }

  console.error(`✅ Command approved by user. Executing...`);

  // Execute the approved command
  const { stdout, stderr } = await execAsync(command, {
    timeout: timeout || 120000,
    maxBuffer: 30 * 1024 * 1024, // 30MB
  });
  
  return stdout + stderr;
}

// Register Bash tool
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: "Bash",
        description: "Execute bash commands with risk-based approval workflow. Low-risk commands execute immediately. Medium/high-risk commands require user approval. Blocked commands are forbidden.",
        inputSchema: {
          type: "object",
          properties: {
            command: {
              type: "string",
              description: "The bash command to execute",
            },
            description: {
              type: "string",
              description: "Description of what this command does",
            },
            timeout: {
              type: "number",
              description: "Optional timeout in milliseconds (max 600000)",
            },
          },
          required: ["command"],
        },
      },
    ],
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (request.params.name === "Bash") {
    const { command, description, timeout } = request.params.arguments;
    
    try {
      const output = await executeBashWithApproval(command, description, timeout);
      
      return {
        content: [
          {
            type: "text",
            text: output || "(command executed successfully, no output)",
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text",
            text: `Error: ${error.message}`,
          },
        ],
        isError: true,
      };
    }
  }
  
  throw new Error(`Unknown tool: ${request.params.name}`);
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Bash Approval MCP Server running on stdio");
}

main().catch((error) => {
  console.error("Fatal error in main():", error);
  process.exit(1);
});
