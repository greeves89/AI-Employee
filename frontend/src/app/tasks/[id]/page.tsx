"use client";

import { useParams } from "next/navigation";
import { TaskDetail } from "@/components/tasks/task-detail";

export default function TaskDetailPage() {
  const params = useParams();
  return <TaskDetail taskId={params.id as string} />;
}
